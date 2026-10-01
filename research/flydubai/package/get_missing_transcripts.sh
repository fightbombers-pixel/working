#!/usr/bin/env bash
# Транскрипти, які YouTube заблокував із хмарного сервера. Запускати на своєму комп'ютері.
# Потрібно: pip install -U yt-dlp ; бути залогіненим у YouTube у Chrome (або змініть chrome на firefox/edge/safari).
set -e
mkdir -p transcripts/missing && cd transcripts/missing
for id in jylFR7Vw61Y 8D1oeZAToFY gzpXVUt8mqg x1s18ZO_vW4 Qi9FDN7CLKc eZoJ7UwMSKY Rs5DNBSVkUY byKJ5GjE8XQ 85cehlWLNa8 dbH58TM1bsA; do
  yt-dlp --cookies-from-browser chrome --skip-download --write-subs --write-auto-subs \
         --sub-langs "en.*" --sub-format vtt -o "%(id)s.%(ext)s" "https://www.youtube.com/watch?v=$id" || echo "FAIL $id"
  sleep 5
done
echo "Готово: файли .vtt у transcripts/missing/"
