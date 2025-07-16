import os
import streamlit as st
import json
import google.generativeai as genai
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import io
import fitz  # PyMuPDF
import docx
import pptx
from PIL import Image
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

# --- Configuration ---
st.set_page_config(page_title="File Analyzer", page_icon="🧩")
st.title("🧩 Google Drive File Analyzer with Gemini")
st.write("Select one or more files from Google Drive for analysis.")

# --- Constants ---
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
REDIRECT_URI = "https://zw2bm6uwryon2f5pnfsauk.streamlit.app"

# --- Helper Functions ---
def get_file_content(drive_service, file_info):
    """Downloads and extracts content from a Google Drive file."""
    file_id = file_info['id']
    mime_type = file_info['mimeType']
    try:
        request = drive_service.files().get_media(fileId=file_id)
        file_bytes = io.BytesIO()
        downloader = MediaIoBaseDownload(file_bytes, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        file_bytes.seek(0)
        
        if 'pdf' in mime_type:
            with fitz.open(stream=file_bytes, filetype="pdf") as doc:
                return "text", "".join(page.get_text() for page in doc)
        elif 'vnd.openxmlformats-officedocument.wordprocessingml.document' in mime_type:
            doc = docx.Document(file_bytes)
            return "text", "\n".join([para.text for para in doc.paragraphs])
        elif 'vnd.openxmlformats-officedocument.presentationml.presentation' in mime_type:
            prs = pptx.Presentation(file_bytes)
            text_runs = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if not shape.has_text_frame: continue
                    for paragraph in shape.text_frame.paragraphs:
                        for run in paragraph.runs: text_runs.append(run.text)
            return "text", "\n".join(text_runs)
        elif 'image' in mime_type:
            Image.open(file_bytes)
            return "image", file_bytes.getvalue()
        elif 'text' in mime_type:
            return "text", file_bytes.getvalue().decode("utf-8", errors='ignore')
        else:
            return "unsupported", f"File type ('{mime_type}')"
    
    except HttpError:
        if 'google-apps' in mime_type:
            if mime_type == 'application/vnd.google-apps.shortcut':
                return "unsupported", "Google Drive Shortcut"
            
            export_mime_type = None
            if mime_type == 'application/vnd.google-apps.document':
                export_mime_type = 'text/plain'
            elif mime_type == 'application/vnd.google-apps.spreadsheet':
                export_mime_type = 'text/csv'
            elif mime_type == 'application/vnd.google-apps.presentation':
                export_mime_type = 'text/plain'

            if export_mime_type:
                try:
                    request = drive_service.files().export_media(fileId=file_id, mimeType=export_mime_type)
                    file_bytes = io.BytesIO()
                    downloader = MediaIoBaseDownload(file_bytes, request)
                    done = False
                    while not done: status, done = downloader.next_chunk()
                    return "text", file_bytes.getvalue().decode("utf-8")
                except HttpError as e: return "unsupported", f"Export Error: {e}"
            else: return "unsupported", f"Google Workspace type ({mime_type})"
        else: return "unsupported", "Google Drive API Error"

def get_gemini_response(api_key, prompt_parts):
    """Sends a multimodal prompt to the Gemini API."""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name="gemini-1.5-pro-latest")
        response = model.generate_content(prompt_parts)
        return response.text
    except Exception as e:
        return f"ERROR: Could not generate response from Gemini. Details: {str(e)}"

# --- Main App Logic ---
if "credentials" not in st.session_state:
    st.session_state.credentials = None

if st.session_state.credentials is None:
    st.subheader("Step 1: Authenticate with Google")
    try:
        client_config = st.secrets["google_credentials"]
        flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI)
        auth_url, _ = flow.authorization_url(prompt="consent")
        st.link_button("Login with Google", auth_url, help="Click to authorize access to your Google Drive files.")
        st.write("After authorizing, you will be redirected back here. If not, copy the full URL and paste it below.")
        redirected_url = st.text_input("Paste the full redirected URL here:")
        if redirected_url:
            code = redirected_url.split('code=')[1].split('&scope')[0]
            flow.fetch_token(code=code)
            creds = flow.credentials
            st.session_state.credentials = creds.to_json()
            st.rerun()
            
    except KeyError:
        st.error('The "google_credentials" secret is missing. Please add it to your Streamlit secrets.')
    except Exception as e:
        st.error("An unexpected error occurred during authentication.")
        st.error(f"Specific error: {e}")
else:
    creds = Credentials.from_authorized_user_info(json.loads(st.session_state.credentials))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        st.session_state.credentials = creds.to_json()

    drive_service = build("drive", "v3", credentials=creds)
    st.success("✅ Connected to Google Drive!")
    if st.button("Logout"):
        st.session_state.credentials = None
        st.rerun()
    st.divider()

    st.subheader("Step 2: Select Files and Ask a Question")
    try:
        results = drive_service.files().list(pageSize=200, fields="files(id, name, mimeType)", q="mimeType != 'application/vnd.google-apps.folder'").execute()
        files = results.get("files", [])
        
        if not files:
            st.write("No files found in your Google Drive.")
        else:
            file_options = {f"{file['name']} ({file['mimeType']})": file for file in files}
            selected_files_display = st.multiselect("Choose files to analyze:", options=list(file_options.keys()))
            user_prompt = st.text_area("What would you like to know about these files?", height=100)

            if st.button("✨ Analyze Files with Gemini", disabled=(not selected_files_display or not user_prompt)):
                prompt_parts = []
                with st.status("Processing files...", expanded=True) as status:
                    for file_display in selected_files_display:
                        status.update(label=f"Processing: {file_display}...")
                        file_info = file_options[file_display]
                        content_type, content = get_file_content(drive_service, file_info)

                        if content_type == 'text':
                            prompt_parts.append(f"\n--- DOCUMENT: {file_info['name']} ---\n{content}")
                        elif content_type == 'image':
                            img = Image.open(io.BytesIO(content))
                            prompt_parts.append(img)
                        else:
                            st.warning(f"Skipping unsupported file: {file_info['name']} ({content})")
                    
                    status.update(label="All files processed!", state="complete")

                if prompt_parts:
                    prompt_parts.insert(0, user_prompt)
                    st.info(f"Sending {len(selected_files_display)} file(s) to Gemini for analysis...")
                    with st.spinner("🤖 Gemini is thinking..."):
                        api_key = st.secrets["GOOGLE_API_KEY"]
                        response = get_gemini_response(api_key, prompt_parts)
                        st.markdown("### 🤖 Gemini Analysis")
                        st.markdown(response)
                else:
                    st.error("No supported files were selected to analyze.")

    except HttpError as error:
        st.error(f"A Google Drive API error occurred: {error}")
        st.info("Your Google authentication may have expired. Please try logging out and logging back in.")
