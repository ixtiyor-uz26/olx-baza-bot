# OLX → JoyEstate baza boti

Kompyuter o'chiq bo'lsa ham har 15 daqiqada GitHub serverida ishlaydi:

1. OLX'dan Toshkent, uzoq muddatli ijara, kvartira, **egasidan**, 450–3000 $ yangi e'lonlarni oladi.
2. Uy egasining raqamini alohida OLX akkaunt orqali oladi (bir ishga tushishda 2 tadan, sekin).
3. Bazada egasini qidiradi. Egasi bor bo'lsa, shu egada xuddi shu uy (xona + qavat) bor-yo'qligini tekshiradi. Uy bor bo'lsa qo'shmaydi.
4. Egasi yo'q bo'lsa yaratadi va uyni rasmlari bilan qo'shadi.
5. "Mijozlar uchun (uy)" va "Xodimlar uchun (uy)" ga bir martadan yuboradi.

OLX "shubhali faollik" desa, bot 3 soat raqam olmaydi, keyin o'zi davom etadi.

## Secrets (Settings → Secrets and variables → Actions)

| Nomi | Nima |
|---|---|
| `JOY_LOGIN` | Bazaga kirish logini |
| `JOY_PASSWORD` | Bazaga kirish paroli |
| `OLX_REFRESH_TOKEN` | Alohida OLX akkauntning tokeni (pastda) |

### OLX tokenini olish
1. Chrome'da alohida OLX akkauntingiz bilan olx.uz ga kiring.
2. F12 bosing → **Console** bo'limi.
3. Quyidagini yozib Enter bosing (token buferga nusxalanadi):

```js
copy(JSON.parse(localStorage[Object.keys(localStorage).find(k=>k.includes('offline_access'))]).body.refresh_token)
```

4. GitHub'da `OLX_REFRESH_TOKEN` secret'iga Ctrl+V qilib saqlang.

Token taxminan 30 kunda eskiradi. Bot "OLX token yangilanmadi" deb to'xtasa, GitHub sizga email yuboradi — shu 4 qadamni qayta bajaring.

## Qo'lda ishga tushirish / sinov
Actions → "OLX -> baza" → Run workflow. `dry_run` ga `1` yozsangiz, bot bazaga hech narsa qo'shmaydi, faqat nima qilishini logda ko'rsatadi.
