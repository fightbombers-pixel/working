#!/usr/bin/env bash
# Транскрипти топ-60 відео про Christa Pike. Запускати на своєму комп'ютері.
# Потрібно: pip install -U yt-dlp ; бути залогіненим у YouTube у Chrome (або змініть chrome на firefox/edge/safari).
mkdir -p transcripts && cd transcripts
for id in d08iOPemh3U MpbwnhHTyy8 SHCRGZDrMRM McRe8jtagNk JavKD2atBeE vJ5IYZAMbU8 2LxL16zmNXw QrVJAKVAbtI 6soZ0Pc9fdA NLYD-vhcOzE D-H9LlFWJoc q9hRIKLKM5s QzuMt9Hv_SQ YSzUI55QKgA uD4h3qCujJ8 E4ed3l0-3kc h3jqH1zY_iw 9DR-Y1xO4sA yTmlgzuIozA hxE3qt8t2YU gy9SdjAU2TI MGRVBmY4Z7k AxLW4Chpn8E 2VqLrJ0pM44 dQUa10TCdC4 XhOKsTYZrmM IkhY_HdM2Ew K-durYzEqqQ 7EcNb1uo0WI qTHhjsbuWHI 62gEV5fM2RY NbwbN0NpisA ruE8JHHSI-4 BOFtu15rCaw tgySM59xnH8 X-Ez_cARboo BJRYO8-g0vY emnSoo1sZyA oUfS8hIJTbM m1ODOcAxB0E ncybRUxY3zQ ruFsQSS7uxo x04CCliXiKE emaw4TcNO48 uYYAJz5TEnU 8FaBWxuwWCY 3USO3GHdme4 tPR_4ngMDJE 6EEXawxqJqI dM9VRsgGh10 6ifK6aBXwuQ gDKu6Pdv40I L9b3wQFOmGo Ryf8YyGEkhM v9aCwyAILBY USPg5VFBcRE Gny0zSEMxIw 7waqQiUBpCk A4_on1soYBI GuRKhSlwTzE; do
  yt-dlp --cookies-from-browser chrome --skip-download --write-subs --write-auto-subs \
         --sub-langs "en.*" --sub-format vtt -o "%(id)s.%(ext)s" "https://www.youtube.com/watch?v=$id" || echo "FAIL $id"
  sleep 5
done
echo "Готово: .vtt файли в transcripts/"
