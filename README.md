# Spotify Library Manager

A personal tool for automatically moving your top tracks from Spotify statistics into your personal library and playlists.

## Features (v1.3 + Playlists)

- Fetch top tracks for **4 weeks / 6 months / all time**
- **Preview with checkboxes** — uncheck any track before adding it
- Fast add without preview (for routine updates)
- **Playlist management**: create new playlists or add tracks to existing ones
- Automatic duplicate checking (both in your library and within playlists)
- Full library cleanup with double confirmation (typing `DELETE` to confirm)
- **Progress bar** for all long-running operations
- **Non-blocking UI** — all operations run smoothly in background threads
- **Automatic retries on rate limits** (429) and temporary 5xx server errors
- Export logs to `.txt`
- Save application settings between sessions
- File logging to `app.log`

---

## Installation

### 1. Clone and Install Dependencies

```bash
cd Shipify
python3 -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate  # Windows
pip install -r requirements.txt

```

### 2. Create a Spotify Application

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/)
2. Click **Create app** → fill in any name and description
3. In **Redirect URIs**, add exactly: `http://127.0.0.1:8080/callback`
4. Save the changes and copy your **Client ID** and **Client Secret**

### 3. Configure `.env`

```bash
cp .env.example .env

```

Open the `.env` file and fill in your `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`.

### 4. Run the Application

```bash
python app.py

```

On the first run, a browser window will open asking you to log in and authorize the app via Spotify. After a successful login, the authentication token will be cached locally in `.spotify_cache`.

---

## Project Structure

```
spotify-library-manager/
├── app.py               # GUI (CustomTkinter)
├── preview_window.py    # Preview window with checkboxes
├── spotify_client.py    # Wrapper around Spotify API (retry, batching, pagination)
├── config.py            # Loads .env configuration
├── requirements.txt
├── .env.example
├── LICENSE
└── .gitignore

```

---

## Under the Hood

* **Architecture**: The GUI is completely decoupled from the business logic. `SpotifyClient` can be used standalone without the UI (e.g., for a CLI tool or cron jobs in the future).
* **Batching**: The Spotify API limits batch requests to 50 IDs at a time. The client automatically splits large lists of tracks into appropriate chunks behind the scenes.
* **Pagination**: A proper pagination strategy is implemented when fetching or modifying the entire library (e.g., keeping `offset=0` during deletion tasks to prevent skipping tracks due to real-time index shifts).
* **Threading**: All heavy blocking network operations run asynchronously inside a `threading.Thread`, while safe UI updates are dispatched back to the main loop via `self.after(0, ...)`.

---

## License

This project is licensed under the [MIT License](LICENSE).

