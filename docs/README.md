# Install

1. Create the folders and config files:

   ```bash
   mkdir -p music data secrets
   cp .env.example .env          # set PUID, PGID, and your paths
   cp keo.example.toml keo.toml  # edit to taste
   ```

   `KEO_TOKEN` and `browser.json` are optional on a fresh install. You can set up your account, YouTube Music login, and Premium cookies directly in the browser.

2. Start Kéo:

   ```bash
   docker compose up -d
   docker compose logs -f keo
   ```

   The image includes Debian's libmediainfo so the library page can show a file's codec and bitrate. A run outside Docker needs `brew install media-info`. Without that library the pages still load and the technical text is absent.

3. Open `http://<server>:8000` in your browser. On a fresh install, Kéo opens `/setup`:

   - Pick your username (defaults to `admin`) and password.
   - Connect YouTube Music by pasting Request Headers from a `music.youtube.com` `/browse` network request or uploading an existing `browser.json` (you can also skip this and add it later under Settings). A free YouTube Music account works for history tracking.
   - If you have YouTube Music Premium, check the Premium box to upload `cookies.txt` and switch downloads to Opus at best bitrate.

   If you prefer to pre-configure `browser.json` or OAuth on the host, put the file in `secrets/` and set `KEO_YTM_AUTH_FILE` in `.env` (and `KEO_TOKEN` if you want a preset sign-in secret). To reach the server from outside your home network, use your own VPN or tunnel.

A choice on the Settings page is saved into `keo.toml` and used on the next download. A hand edit of `keo.toml` still needs `docker compose restart keo`.

# Resetting your password

If you forget your username or password:

1. Add `KEO_RESET_PASSWORD=1` to `.env`.
2. Restart the container with `docker compose up -d`.
3. If `KEO_TOKEN` is unset, open `/setup` in the browser to choose a new username and password. If `KEO_TOKEN` is set, sign in with `admin` (or `KEO_USERNAME`) and your `KEO_TOKEN`.
4. Remove `KEO_RESET_PASSWORD=1` from `.env` and restart again so Kéo does not reset the password on the next boot.

Resetting your password leaves your library and API key untouched.

# Development

To build from local source instead of pulling the image:

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
```

