# Install

1. Create the folders and settings:

   ```bash
   mkdir -p music data secrets
   cp .env.example .env          # set KEO_TOKEN, PUID, PGID, and the paths
   cp keo.example.toml keo.toml  # edit to taste
   ```

2. Create the YouTube Music login file. Follow the header steps in the
   [ytmusicapi browser setup](https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html), then run:

   ```bash
   python3 -m venv .venv && .venv/bin/pip install ytmusicapi
   .venv/bin/ytmusicapi browser --file secrets/browser.json
   ```

   The account does not need Premium for history.

3. YouTube Music Premium only: export `cookies.txt` for `https://music.youtube.com` into `secrets/`. Then in `keo.toml` set `cookie_file = "/secrets/cookies.txt"`, `format = "opus"` (or `"m4a"`), and `bitrate = "disable"`.

4. Start it:

   ```bash
   docker compose up -d --build
   docker compose logs -f keo
   ```

5. Open `http://<server>:8000` on your phone and sign in with `KEO_TOKEN`. To reach the server from outside your home network, use your own VPN or tunnel.

Kéo reads `keo.toml` only when it starts. Run `docker compose restart keo` after you edit it.
