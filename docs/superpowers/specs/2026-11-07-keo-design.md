# Kéo

Kéo is a personal control panel that finds music on YouTube Music and downloads it with spotDL. Files stay on the Mac or home server that runs the app. The phone browser searches, browses, and starts downloads. It does not play audio.

Version 1 is one FastAPI process in one Docker container. A later native phone app can call the same JSON API. This document is the spec for that version.

The `ytmusicapi` and `spotify-downloader` directories in this workspace are local reference clones. Kéo installs both as package dependencies. It does not import those trees.

## Decisions

- One shared token, checked on every request. Reaching the server from outside the house is a VPN or tunnel the operator sets up. Kéo does not provide that tunnel.
- One operator. Settings live in `keo.toml` on the server. The phone cannot change them. A restart loads the file.
- Defaults suit a regular YouTube account: format `mp3`, bitrate `128k`, no cookie file. YouTube Music Premium audio is an optional commented block: `cookies.txt`, format `opus` or `m4a`, and bitrate `disable`. spotDL caps non-premium audio at 128 kbps. A higher bitrate only re-encodes that audio into a larger file. Premium cookies plus `opus` or `m4a` plus bitrate `disable` keep the 256 kbps file.
- History login and premium audio are different files. `get_history` needs a `ytmusicapi` auth file. That account does not have to be Premium. `cookies.txt` is only for premium audio and is optional.
- A manual tap downloads the top spotDL match even when the names differ. The daily history job downloads immediately only on a close match. A weak match or no metadata result waits on the review list.
- A song qualifies for that job at 3 or more plays labeled `Today` for the same `videoId`. Two plays are logged and do not download.
- There is no playback, no native app, no user accounts, and no “download the whole chart.”

## Architecture

The phone talks only to FastAPI. FastAPI uses four pieces in the same process.

| Piece | Does | Depends on |
| --- | --- | --- |
| Catalog | Search, suggestions, moods, Vietnam charts, playlist tracks, radio, history | `ytmusicapi`, auth file, language `en` |
| Library | Songs already owned, jobs, review rows, history pulls | SQLite file in the data volume |
| Downloader | One `spotdl` command at a time | Library, `keo.toml`, music volume, data volume, optional cookie file |
| Scheduler | History check once a day, plus a catch-up run | Catalog, Library, Downloader |

Downloader in that table is the `jobs` queue plus `spotdl_cmd`. `web` is the HTTP front of the same process.

spotDL looks up public song metadata on its own. The operator does not log in to Spotify. Optional `client_id` and `client_secret` in `keo.toml` exist for when that public lookup fails. Empty values mean the public client.

Compose sets these environment variables:

| Variable | Meaning |
| --- | --- |
| `KEO_TOKEN` | Shared secret |
| `KEO_MUSIC_DIR` | Music volume mount |
| `KEO_DATA_DIR` | SQLite file and the spotDL archive |
| `KEO_YTM_AUTH` | Path to `browser.json` or the OAuth file |
| `KEO_CONFIG` | Path to `keo.toml` |

Credential files are mounted read-only. Kéo does not load a spotDL `config.json`. The container home has none, so a leftover spotDL config cannot override the library rules.

## Modules

Each module has one job. Callers use the functions below and do not reach past them.

- `config` loads the environment and `keo.toml`, checks them, and returns a settings object. It reads the filesystem and nothing else.
- `catalog` returns plain song records: `video_id`, `title`, `artists`, `album`. `artists[0]` is the primary artist. Playlist pages come from `get_playlist`. Radio comes from `get_watch_playlist`. Charts use `get_charts("VN")`. Suggestions use `get_search_suggestions`. History uses `get_history`. It depends on `ytmusicapi`.
- `match` is pure. `normalize(text)` and `is_close(heard, candidate)` do no I/O.
- `library` is the only module that writes SQLite.
- `spotdl_cmd` builds the argument list and runs `spotdl save` or `spotdl download`. It depends on settings and the two directories.
- `jobs` is the queue. It asks `spotdl_cmd` for metadata, then downloads the Spotify URL from that metadata. It depends on `library`, `match`, and `spotdl_cmd`.
- `history` runs one pull. It depends on `catalog`, `library`, `match`, and `jobs`.
- `web` serves the phone pages and the JSON API. It checks the token and calls the modules above.

## Name matching

`normalize` applies these steps in order:

1. Unicode NFKC, then casefold.
2. Drop a parenthetical or bracket group whose text starts with `feat` or `ft`.
3. Drop a trailing `feat` or `ft` and everything after it.
4. Drop punctuation. Keep letters, numbers, and spaces.
5. Collapse whitespace and trim.

Titles are close when their normalized forms are equal.

Artists are close when the normalized primary artists are equal, or when the shorter one appears inside the longer one as a whole word or a whole run of words. `jimin` matches `park jimin`. `min` does not match `jimin`.

A pair is a close match when both the title and the artist are close. The candidate primary artist is `artists[0]`, or `artist` when the list is empty.

## spotDL settings

`keo.toml` holds the operator’s spotDL choices and the history clock. The shipped example uses the regular-user defaults and includes a commented premium block.

The operator can set:

- Quality: `format` (`mp3`, `m4a`, `opus`, `ogg`, `flac`, `wav`), `bitrate`, `audio` (`youtube-music`, `youtube`, `soundcloud`, `bandcamp`, `piped`), `sponsor_block`, `ffmpeg_args`
- Lyrics: `lyrics` (`genius`, `musixmatch`, `azlyrics`, `synced`), `genius_access_token`, `generate_lrc`, `id3_separator`, `skip_album_art`
- Matching: `search_query`, `album_type`, `only_verified_results`, `dont_filter_results`, `skip_explicit`
- Files: `output_template` (default `{artists} - {title}.{output-ext}`), `restrict`, `max_filename_length`
- Advanced: `proxy`, `yt_dlp_args`, `preload`, `max_retries`, `client_id`, `client_secret`
- Clock: `history_timezone` (default `Asia/Ho_Chi_Minh`), `history_hour` (default `23`, meaning 23:00)

Kéo always passes `download` for the audio step, the query or Spotify URL it chose, an output directory of `KEO_MUSIC_DIR`, `--archive` under `KEO_DATA_DIR`, `--overwrite skip`, `--detect-formats mp3 m4a opus ogg flac wav`, and `--threads 1`. An empty `cookie_file` omits `--cookie-file`.

Kéo does not expose `web`, `sync`, `meta`, `save`, or `url` as things the operator can turn on. It also does not expose Spotify library queries, m3u, playlist artwork flags, `--fetch-albums`, skip-files, or spotDL’s host, port, and TLS flags. `save` is an internal metadata step, not an operator mode.

## Data model

`history_pulls`: `id`, `started_at`, `status` (`ok` or `failed`), `error`.

`history_items`: `id`, `pull_id`, `video_id`, `artist`, `title`, `played_label`. One row per item returned in that pull, including repeated plays of the same video.

`songs`: `video_id` unique and nullable, normalized `artist_key` and `title_key` unique together, `spotify_id` unique and nullable, display artist and title. A row means the song is owned.

`jobs`: `video_id`, `spotify_url`, `spotify_id`, display names, `status` (`queued`, `running`, `succeeded`, `failed`), `source` (`manual`, `history`, `review`), `error`, timestamps. At most one `queued` or `running` job exists for a given `video_id`.

`review`: one row per `video_id`. Heard name, candidate name, `spotify_url`, `spotify_id`, and `reason` (`weak_match` or `no_metadata`). A row stays until that song is owned.

The play count for a decision is the number of `history_items` in the latest `ok` pull with `played_label` equal to `Today` and that `video_id`.

## Data flow

**Manual download.** Search, a Vietnam chart, a mood playlist, or radio lists songs. One tap creates one job and the request returns. The worker runs `spotdl save` with the operator’s matching settings and the query `"{primary artist} - {title}"`. Stdout is a JSON array of song objects. The first object is the candidate. Kéo reads `name`, `artist`, `artists`, `url`, and `song_id`. It then runs `spotdl download` on that Spotify `url` with the operator’s quality settings. The tap is the confirmation, so a weak name match still downloads. Log lines that are not the JSON array are ignored. Empty stdout, invalid JSON, or an empty array is no metadata result: the manual job fails.

**History.** At 23:00 by default, using `history_hour` in `history_timezone`, and also at startup when that hour has already passed and no `ok` pull exists for that local date, Kéo stores a pull. A video qualifies when its `Today` count in that pull is 3 or more, the song is not owned, and it is not already `queued` or `running`.

For each qualifier, the worker does the same metadata lookup. A close match is queued for download of that Spotify URL with source `history`. The review row for that `video_id`, if one exists, is removed when the song becomes owned. A weak match or no metadata result inserts one review row when that `video_id` is not already on the list, and leaves an existing row unchanged.

**Owned.** Before lookup, a song is owned when its `video_id` is in `songs`, or its normalized artist and title are in `songs`, or its Spotify id is in `songs` or in the spotDL archive. During download, `--overwrite skip` and `--detect-formats` also treat an existing file in the music folder as owned. Exit code 0 marks the song owned, whether spotDL wrote a new file or skipped one that was already there.

**Review tap.** Downloads the stored Spotify URL. It does not search again. A `no_metadata` row has no URL, so its action is a new metadata lookup using the heard name, and the usual close or weak result.

## HTTP

The phone pages are server-rendered HTML: Search, Charts, Moods, Review, and Jobs. Radio opens from a song. Charts are Vietnam only.

The browser signs in by posting the token to `/login`. A match sets an httpOnly session cookie that lasts 30 days. The JSON API accepts that cookie or `Authorization: Bearer <KEO_TOKEN>`. Both checks use a constant-time compare.

| Page | API |
| --- | --- |
| `GET /` search | `GET /api/search?q=` and `GET /api/suggestions?q=` |
| `GET /charts` | `GET /api/charts` |
| `GET /moods` and `GET /moods/{params}` | `GET /api/moods` and `GET /api/moods/{params}` |
| `GET /playlists/{id}` | `GET /api/playlists/{id}` |
| `GET /songs/{videoId}/radio` | `GET /api/songs/{videoId}/radio` |
| `POST` a download from any song row | `POST /api/downloads` with `video_id`, `title`, `artists` |
| `GET /review` | `GET /api/review` and `POST /api/review/{videoId}/download` |
| `GET /jobs` | `GET /api/jobs` and `POST /api/jobs/{id}/retry` |

`POST /api/downloads` returns the existing job when that `video_id` is already `queued` or `running`.

## Error handling

Startup exits before serving when `KEO_TOKEN`, `KEO_MUSIC_DIR`, or `KEO_YTM_AUTH` is missing, when `keo.toml` is invalid, when `generate_lrc` is true and `synced` is not in `lyrics`, or when `cookie_file` is set and the file is missing. The message names the setting.

A missing or wrong token is 401. YouTube Music down, or an auth file it rejects, is 502 with a short reason, and the page shows that text. An unknown job is 404.

A failed download stores the last 40 lines of spotDL output on the job, does not insert a `songs` row, and the worker moves to the next job. Nothing retries by itself. Retry queues that same Spotify URL. The process stays up when a cookie or login expires during a download.

If `get_history` fails, the pull is stored as `failed`, the review list and `songs` stay unchanged, and the next attempt is the next scheduled run. One song’s metadata or download error does not stop the other qualifiers in that pull. A close match that fails to download is a `failed` job. A weak match or no metadata result is a review row.

## Testing

Automated tests do not call YouTube or Spotify. Catalog and the spotDL process are fakes. A real download is a manual check after the container is up.

Pure tests cover `normalize` and `is_close`, the 3-play `Today` rule, library ownership, startup rejection of a bad `keo.toml`, and the argument builder. The builder always includes the owned flags. A regular config omits `--cookie-file` and uses `mp3` and `128k`. A premium config passes the cookie path, `opus` or `m4a`, and bitrate `disable`.

Job tests cover one tap creating one job, a second tap reusing it, exit 0 marking the song owned, a non-zero exit marking the job `failed` without a `songs` row and then continuing, and Retry requeueing the same URL.

History tests cover a close match queued, a weak match or no metadata sent to review, owned songs skipped, one song’s failure leaving the rest of the pull running, and a failed `get_history` leaving review and `songs` unchanged.

HTTP tests cover 401 and 404 for the routes above.

## Build order

The phone pages stay a thin view over the JSON API. Each page is built in the same slice as the API it calls. Charts, moods, radio, and history wait until one manual download works, because they share that queue.

1. Settings, the library, name matching, and the spotDL command, with tests and no pages.
2. The download queue, then Search: login, the token check, `GET /api/search`, `GET /api/suggestions`, `POST /api/downloads`, and the Search page together.
3. Charts, moods, playlists, and radio, reusing that download action.
4. Review, Jobs, and the daily history check.

## Done

Version 1 is done when the container starts with the example config, the five pages work behind the token, a tap downloads one song through spotDL, a second tap does not double-download, a file already in the library is skipped, and the daily history job follows the 3-play close-match rule.
