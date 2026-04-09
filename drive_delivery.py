"""
Google Drive delivery — upload sorted deliverables and generate share links.

First run will open a browser for OAuth consent (Drive scope).
Subsequent runs reuse the saved token at ~/.sortie/drive_token.json.

Requirements:
    pip install google-api-python-client google-auth-oauthlib
"""

import os
import json
import mimetypes
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

TOKEN_PATH = Path.home() / ".sortie" / "drive_token.json"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# OAuth client — registered under Faith & Harmony GCP project.
# drive.file scope only grants access to files this app creates, not the full Drive.
_CLIENT_CONFIG = {
    "installed": {
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        "redirect_uris": ["http://localhost:8085/"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


def _get_credentials():
    """Return valid Google credentials, prompting browser auth if needed."""
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds)
    elif not creds or not creds.valid:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_config(_CLIENT_CONFIG, SCOPES)
        creds = flow.run_local_server(port=8085)
        _save_token(creds)

    return creds


def _save_token(creds):
    """Persist token to disk."""
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())


def _build_service(creds):
    """Build the Drive v3 API service."""
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=creds)


def is_authenticated():
    """Check if we have a valid (or refreshable) Drive token."""
    if not TOKEN_PATH.exists():
        return False
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        if creds.valid:
            return True
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_token(creds)
            return True
    except Exception:
        pass
    return False


def authenticate():
    """Run the OAuth flow (opens browser). Returns True on success."""
    try:
        _get_credentials()
        return True
    except Exception as e:
        raise RuntimeError(f"Google Drive auth failed: {e}") from e


def create_delivery_folder(service, site_name, parent_id=None):
    """Create a folder in Drive. Returns folder ID."""
    meta = {
        "name": site_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        meta["parents"] = [parent_id]
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def upload_file(service, local_path, folder_id, progress_callback=None):
    """Upload a single file to the given Drive folder. Returns file ID."""
    from googleapiclient.http import MediaFileUpload

    path = Path(local_path)
    mime, _ = mimetypes.guess_type(str(path))
    if mime is None:
        mime = "application/octet-stream"

    meta = {"name": path.name, "parents": [folder_id]}
    media = MediaFileUpload(str(path), mimetype=mime, resumable=True,
                            chunksize=10 * 1024 * 1024)

    request = service.files().create(body=meta, media_body=media, fields="id")

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status and progress_callback:
            progress_callback(status.progress())

    return response["id"]


def share_folder(service, folder_id, role="reader"):
    """Make a folder accessible via link. Returns the web link."""
    permission = {"type": "anyone", "role": role}
    service.permissions().create(fileId=folder_id, body=permission).execute()
    folder = service.files().get(fileId=folder_id, fields="webViewLink").execute()
    return folder["webViewLink"]


def deliver(output_dir, site_name, parent_folder_id=None,
            progress_callback=None):
    """
    Upload all files from output_dir to a new Drive folder and share it.

    Args:
        output_dir: Local path with sorted deliverables
        site_name: Name for the Drive folder
        parent_folder_id: Optional Drive folder ID to nest under
        progress_callback: fn(current_file, total_files, filename, upload_pct)

    Returns:
        dict with keys: folder_id, share_link, file_count, folder_name
    """
    creds = _get_credentials()
    service = _build_service(creds)

    # Collect all files to upload (walk subdirectories)
    all_files = []
    output_path = Path(output_dir)
    for root, dirs, files in os.walk(output_path):
        for f in files:
            all_files.append(Path(root) / f)

    if not all_files:
        raise ValueError(f"No files found in {output_dir}")

    # Create top-level delivery folder
    folder_id = create_delivery_folder(service, site_name, parent_folder_id)

    # Map subdirectory names to Drive folder IDs (for preserving structure)
    subfolder_ids = {}

    total = len(all_files)
    for i, fpath in enumerate(all_files):
        # Determine if file is in a subdirectory
        rel = fpath.relative_to(output_path)
        if len(rel.parts) > 1:
            # File is in a subfolder — create it in Drive if we haven't already
            sub_name = rel.parts[0]
            if sub_name not in subfolder_ids:
                subfolder_ids[sub_name] = create_delivery_folder(
                    service, sub_name, folder_id)
            target_folder = subfolder_ids[sub_name]
        else:
            target_folder = folder_id

        def file_progress(pct):
            if progress_callback:
                progress_callback(i + 1, total, fpath.name, pct)

        upload_file(service, str(fpath), target_folder, file_progress)

        if progress_callback:
            progress_callback(i + 1, total, fpath.name, 1.0)

    # Share the folder
    link = share_folder(service, folder_id)

    return {
        "folder_id": folder_id,
        "share_link": link,
        "file_count": total,
        "folder_name": site_name,
    }


def set_client_credentials(client_id, client_secret):
    """Configure OAuth client credentials (call once during setup)."""
    _CLIENT_CONFIG["installed"]["client_id"] = client_id
    _CLIENT_CONFIG["installed"]["client_secret"] = client_secret


def load_client_credentials():
    """Load OAuth client ID/secret from sortie_settings or env vars."""
    # Try env vars first
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    if not client_id:
        # Try sortie settings
        settings_path = Path(__file__).resolve().parent / "sortie_settings.json"
        if settings_path.exists():
            try:
                with open(settings_path) as f:
                    s = json.load(f)
                client_id = s.get("google_client_id", "")
                client_secret = s.get("google_client_secret", "")
            except (json.JSONDecodeError, KeyError):
                pass

    if client_id and client_secret:
        set_client_credentials(client_id, client_secret)
        return True
    return False


# Auto-load on import
load_client_credentials()
