"""
OLX -> JoyEstate baza avtomatik import boti.

Har ishga tushganda:
  1. OLX'dan Toshkent, uzoq muddatli ijara, kvartira, egasidan, 450-3000$ e'lonlarini oladi.
  2. Yangi (oxirgi 48 soatda qo'yilgan, hali ko'rilmagan) e'lonlarni navbatga qo'shadi.
  3. Navbatdan bir nechtasini oladi: uy egasining raqamini OLX'dan oladi (alohida akkaunt orqali).
  4. Bazada egasini tekshiradi: bor bo'lsa — shu egada shu uy bor-yo'qligini tekshiradi;
     yo'q bo'lsa — egasini yaratadi.
  5. Uyni rasmlari bilan qo'shadi va "Mijozlar uchun (uy)" + "Xodimlar uchun (uy)" ga yuboradi.

Sozlamalar GitHub Secrets orqali beriladi (README.md ga qarang).
"""
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ---------------- Sozlamalar ----------------
JOY_API = "https://api.v2.joyestate.uz"
JOY_LOGIN = os.environ.get("JOY_LOGIN", "")
JOY_PASSWORD = os.environ.get("JOY_PASSWORD", "")
OLX_REFRESH_TOKEN = os.environ.get("OLX_REFRESH_TOKEN", "")

OLX_CLIENT_ID = "4b7edpvrarh6co2rp6lhae0jva"
OLX_TOKEN_URL = "https://login.olx.uz/oauth2/token"
OLX_API = "https://www.olx.uz/api/v1"
OLX_CATEGORY = 2155          # Kvartiralar -> Uzoq muddatli ijara
OLX_CITY = 4                 # Toshkent
PRICE_FROM, PRICE_TO = 450, 3000

MAX_PHONES_PER_RUN = int(os.environ.get("MAX_PHONES_PER_RUN", "2"))
NEW_WINDOW_HOURS = 48        # faqat shuncha soat ichida qo'yilgan e'lonlar
COOLDOWN_HOURS = 3           # OLX "shubhali faollik" desa, shuncha soat dam oladi
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"

TG_CONFIGS = {  # bazadagi Telegram sozlamalari (ijara, uy)
    "Mijozlar uchun (uy)": "6ad522ed015234de48235120",
    "Xodimlar uchun (uy)": "6ad52226015234de4823511c",
}
REPAIR_IDS = {
    "designer": "6ad48b78015234de4823500b",
    "euro": "6ad48ba6015234de4823500e",
    "hitech": "6ad48b8b015234de4823500c",
    "mid": "6ad48b99015234de4823500d",
}
AMENITY_KEYS = ["ac", "tv", "washing_machine", "fridge", "internet",
                "parking", "elevator", "gas", "phone_line"]
AMENITY_WORDS = {
    "ac": ["кондиционер"], "tv": ["телевизор"], "washing_machine": ["стиральная"],
    "fridge": ["холодильник"], "internet": ["интернет"], "parking": ["парковк", "стоянк"],
    "elevator": ["лифт"], "gas": ["газ"], "phone_line": ["телефон"],
}
DISTRICTS = {  # OLX tuman nomi (bo'lagi) -> baza region
    "алмазар": "almazar", "бектемир": "bektemir", "мирабад": "mirobod",
    "мирзо-улугбек": "mirzo_ulugbek", "сергели": "sergeli", "учтепин": "uchtepa",
    "чиланзар": "chilanzar", "шайхантах": "shayxontoxur", "юнусабад": "yunusobod",
    "яккасарай": "yakkasaroy", "яшнабад": "yashnobod",
    "янгихаёт": "sergeli",  # bazada Yangihayot yo'q — Sergeli ga qo'yiladi
}

STATE_FILE = Path(__file__).parent / "state" / "state.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


# ---------------- Holat (state) ----------------
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"seen": {}, "queue": [], "cooldown_until": None, "initialized": False}


def save_state(st):
    # 10 kundan eski ID larni tozalaymiz
    cutoff = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    st["seen"] = {k: v for k, v in st["seen"].items() if v >= cutoff}
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------------- OLX ----------------
olx = requests.Session()
olx.headers.update({"User-Agent": UA, "Accept": "application/json",
                    "Accept-Language": "ru-RU,ru;q=0.9"})


def olx_access_token():
    r = requests.post(OLX_TOKEN_URL, data={
        "grant_type": "refresh_token", "client_id": OLX_CLIENT_ID,
        "refresh_token": OLX_REFRESH_TOKEN}, headers={"User-Agent": UA}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"OLX token yangilanmadi ({r.status_code}): {r.text[:200]}. "
                           "OLX_REFRESH_TOKEN ni yangilang (README).")
    return r.json()["access_token"]


def olx_list_new():
    """Oxirgi e'lonlar (bir necha sahifa)."""
    out = []
    for offset in (0, 50, 100):
        r = olx.get(f"{OLX_API}/offers/", params={
            "offset": offset, "limit": 50, "category_id": OLX_CATEGORY, "city_id": OLX_CITY,
            "owner_type": "private", "currency": "UYE",
            "filter_float_price:from": PRICE_FROM, "filter_float_price:to": PRICE_TO,
            "sort_by": "created_at:desc"}, timeout=30)
        r.raise_for_status()
        data = r.json().get("data", [])
        out += data
        if len(data) < 50:
            break
        time.sleep(random.uniform(1, 3))
    return out


def olx_offer(ad_id):
    r = olx.get(f"{OLX_API}/offers/{ad_id}/", timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()["data"]


class Suspicious(Exception):
    pass


def olx_phone(ad_id, token):
    r = olx.get(f"{OLX_API}/offers/{ad_id}/limited-phones/",
                headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code == 400 and ("подозрительн" in r.text or "suspicious" in r.text.lower()):
        raise Suspicious(r.text[:200])
    if r.status_code != 200:
        log(f"  raqam olinmadi {ad_id}: {r.status_code} {r.text[:150]}")
        return None
    phones = r.json().get("data", {}).get("phones", [])
    for p in phones:
        digits = re.sub(r"\D", "", p)
        if len(digits) >= 9:
            return "+998" + digits[-9:]
    return None


def param(offer, key):
    for p in offer.get("params", []):
        if p["key"] == key:
            return p.get("value") or {}
    return {}


def pval(offer, key):
    v = param(offer, key)
    for k in ("key", "value", "label"):
        if v.get(k) not in (None, ""):
            return str(v[k]).split(" ")[0]
    return None


def num(v):
    try:
        return float(str(v).replace(",", ".").replace(" ", ""))
    except Exception:
        return None


def clean_text(t):
    t = re.sub(r"<[^>]+>", " ", t or "")
    t = re.sub(r"(\+?998)?[\s\-()]*\d{2}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}", "", t)  # raqamlarni olib tashlash
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def map_offer(o):
    """OLX e'lonini baza maydonlariga aylantiradi. Mos kelmasa None qaytaradi."""
    price = param(o, "price")
    value = num(price.get("value"))
    currency = "USD" if price.get("currency") in ("UYE", "USD") else "UZS"
    if not value:
        return None
    district = (o.get("location", {}).get("district") or {}).get("name", "").lower()
    region = next((v for k, v in DISTRICTS.items() if k in district), None)
    if not region:
        return None
    rooms = num(pval(o, "number_of_rooms"))
    floor = num(pval(o, "floor"))
    floors = num(pval(o, "total_floors"))
    area = num(pval(o, "total_area")) or 0
    repair_label = (param(o, "repairs").get("label") or "").lower()
    repair = ("designer" if "автор" in repair_label else
              "euro" if "евро" in repair_label else
              "hitech" if "хай" in repair_label or "hi" in repair_label else "mid")
    more = (param(o, "comfort").get("label") or param(o, "more").get("label") or "").lower()
    amen = {k: any(w in more for w in words) for k, words in AMENITY_WORDS.items()}
    furnished = (param(o, "furnished").get("label") or "").lower().startswith("да")

    desc = clean_text(o.get("description"))
    waymark = ""
    m = re.search(r"(ориентир|мўлжал|mo'ljal|орентир)[:\s-]*([^\n.]{3,80})", desc, re.I)
    if m:
        waymark = m.group(2).strip()
    title = clean_text(o.get("title"))[:120]
    return {
        "price": value, "currency": currency, "region": region,
        "address": title or "OLX", "waymark": waymark,
        "area": int(area) if area else 1, "rooms": int(rooms) if rooms else None,
        "floor": int(floor) if floor else None, "building_floors": int(floors) if floors else None,
        "repair": repair, "amenities": amen, "is_furnished": furnished,
        "description": desc[:3000],
        "photos": [p["link"].replace("{width}", "1200").replace("{height}", "900")
                   for p in o.get("photos", [])][:9],
        "owner_name": ((o.get("user") or {}).get("name") or "Egasi").strip()[:40],
    }


# ---------------- JoyEstate baza ----------------
class Joy:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA})
        r = self.s.post(f"{JOY_API}/auth/login",
                        json={"identifier": JOY_LOGIN, "password": JOY_PASSWORD}, timeout=30)
        if r.status_code != 200 or "accessToken" not in r.json():
            raise RuntimeError(f"Bazaga kirib bo'lmadi ({r.status_code}). JOY_LOGIN/JOY_PASSWORD ni tekshiring.")
        self.s.headers["Authorization"] = "Bearer " + r.json()["accessToken"]

    def find_landlord(self, phone):
        r = self.s.get(f"{JOY_API}/users/agent/landlords",
                       params={"page": 1, "limit": 10, "search": phone[-9:]}, timeout=30)
        r.raise_for_status()
        for u in r.json().get("data", {}).get("users", []):
            if re.sub(r"\D", "", u.get("phone_number") or "")[-9:] == phone[-9:]:
                return u
        return None

    def landlord_has_same(self, landlord_id, m):
        r = self.s.get(f"{JOY_API}/properties/agent/rent", params={
            "page": 1, "limit": 100, "landlord": landlord_id, "property_type": "house"}, timeout=30)
        r.raise_for_status()
        for p in r.json().get("properties", []):
            same_rooms = str(p.get("rooms")) == str(m["rooms"])
            same_floor = str(p.get("floor")) == str(m["floor"]) and \
                str(p.get("building_floors")) == str(m["building_floors"])
            if same_rooms and (same_floor or m["floor"] is None):
                return p.get("property_id")
        return None

    def create_landlord(self, name, phone):
        r = self.s.post(f"{JOY_API}/users/agent/landlords",
                        json={"full_name": name, "phone_number": phone}, timeout=30)
        r.raise_for_status()
        return r.json()["data"]

    def create_property(self, m, landlord_id, images):
        f = {
            "price": f"{m['price']:.2f}", "currency": m["currency"], "transaction_type": "for_rent",
            "region": m["region"], "address": m["address"], "area": str(m["area"]),
            "description": m["description"], "landlord": landlord_id,
            "is_furnished": str(m["is_furnished"]).lower(), "status": "active",
            "advance_payment_required": "false", "deposit_required": "false", "deposit_value": "",
            "date_of_availability": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "repair": REPAIR_IDS[m["repair"]],
        }
        if m["waymark"]:
            f["waymark"] = m["waymark"]
        for k in ("rooms", "floor", "building_floors"):
            if m[k] is not None:
                f[k] = str(m[k])
        for k in AMENITY_KEYS:
            f[f"amenities[{k}]"] = str(m["amenities"].get(k, False)).lower()
        files = [("main_image", ("main.jpg", images[0], "image/jpeg"))]
        files += [("album_images", (f"{i}.jpg", b, "image/jpeg")) for i, b in enumerate(images[1:9])]
        r = self.s.post(f"{JOY_API}/properties/agent/house", data=f, files=files, timeout=120)
        if r.status_code >= 300:
            raise RuntimeError(f"Uy qo'shilmadi: {r.status_code} {r.text[:300]}")
        return r.json()["property"]

    def send_telegram(self, prop_mongo_id):
        for name, cfg in TG_CONFIGS.items():
            r = self.s.post(f"{JOY_API}/telegram/send/{cfg}",
                            json={"property_type": "house", "property_id": prop_mongo_id}, timeout=120)
            log(f"  Telegram '{name}': {r.status_code}")
            time.sleep(3)


def download_images(urls):
    out = []
    for u in urls:
        try:
            r = requests.get(u, headers={"User-Agent": UA}, timeout=30)
            if r.status_code == 200 and r.content:
                out.append(r.content)
        except Exception as e:
            log("  rasm yuklanmadi:", e)
    return out


# ---------------- Asosiy ----------------
def main():
    st = load_state()
    now = datetime.now(timezone.utc)

    # 1) Yangi e'lonlarni navbatga qo'shish
    ads = olx_list_new()
    window = now - timedelta(hours=NEW_WINDOW_HOURS)
    added = 0
    for a in ads:
        aid = str(a["id"])
        if aid in st["seen"]:
            continue
        st["seen"][aid] = now_iso()
        created = datetime.fromisoformat(a["created_time"])
        if not st["initialized"] and created < now - timedelta(hours=6):
            continue  # birinchi ishga tushishda eski e'lonlarni olmaymiz
        if created >= window:
            st["queue"].append({"id": aid, "created": a["created_time"]})
            added += 1
    st["initialized"] = True
    # navbatdan 3 kundan eskilarini olib tashlash, eng yangisi oldinda
    st["queue"] = [q for q in st["queue"]
                   if datetime.fromisoformat(q["created"]) >= now - timedelta(days=3)]
    st["queue"].sort(key=lambda q: q["created"], reverse=True)
    log(f"OLX: {len(ads)} ta e'lon ko'rildi, {added} ta yangi navbatga qo'shildi. Navbatda: {len(st['queue'])}")

    if st.get("cooldown_until") and now < datetime.fromisoformat(st["cooldown_until"]):
        log(f"OLX dam olish rejimida ({st['cooldown_until']} gacha). Raqam olinmaydi.")
        save_state(st)
        return
    if not st["queue"]:
        save_state(st)
        return

    try:
        process_queue(st, now)
    finally:
        save_state(st)


def process_queue(st, now):
    token = olx_access_token()
    joy = Joy()
    phones_used = 0
    while st["queue"] and phones_used < MAX_PHONES_PER_RUN:
        item = st["queue"].pop(0)
        aid = item["id"]
        log(f"E'lon {aid}:")
        offer = olx_offer(aid)
        if not offer:
            log("  e'lon o'chirilgan")
            continue
        m = map_offer(offer)
        if not m or not m["photos"]:
            log("  mos emas (tuman/narx/rasm yo'q) — o'tkazib yuborildi")
            continue
        try:
            phone = olx_phone(aid, token)
        except Suspicious as e:
            log("  OLX shubhali faollik dedi — dam olamiz:", e)
            st["queue"].insert(0, item)
            st["cooldown_until"] = (now + timedelta(hours=COOLDOWN_HOURS)).isoformat()
            break
        phones_used += 1
        if not phone:
            continue

        landlord = joy.find_landlord(phone)
        if landlord:
            dup = joy.landlord_has_same(landlord["_id"], m)
            if dup:
                log(f"  bazada bor (#{dup}) — qo'shilmadi")
                continue
            landlord_id = landlord["_id"]
            log(f"  egasi bazada bor: {landlord.get('full_name')}")
        else:
            landlord_id = None

        images = download_images(m["photos"])
        if not images:
            log("  rasmlar yuklanmadi — o'tkazib yuborildi")
            continue
        if DRY_RUN:
            log(f"  [DRY_RUN] qo'shilardi: {m['rooms']} xona, {m['price']} {m['currency']}, {m['region']}, {phone}")
            continue
        if not landlord_id:
            landlord_id = joy.create_landlord(f"{m['owner_name']} (OLX)", phone)["_id"]
            log("  yangi egasi yaratildi")
        prop = joy.create_property(m, landlord_id, images)
        log(f"  QO'SHILDI: #{prop.get('property_id')} — {m['rooms']} xona, {m['price']:.0f} {m['currency']}, {m['region']}")
        joy.send_telegram(prop["_id"])
        if st["queue"] and phones_used < MAX_PHONES_PER_RUN:
            time.sleep(random.uniform(40, 90))  # OLX shubhalanmasligi uchun


if __name__ == "__main__":
    missing = [k for k in ("JOY_LOGIN", "JOY_PASSWORD", "OLX_REFRESH_TOKEN") if not os.environ.get(k)]
    if missing:
        print("Yetishmayapti (GitHub Secrets):", ", ".join(missing))
        sys.exit(1)
    main()
