#!/data/data/com.termux/files/usr/bin/bash
# Botni har 15 daqiqada ishga tushiradi. Termux'da: bash ishga_tushir.sh
cd "$(dirname "$0")"
termux-wake-lock 2>/dev/null
while true; do
  git pull -q 2>/dev/null
  python bot.py
  echo "--- keyingi tekshiruv 15 daqiqadan keyin ---"
  sleep 900
done
