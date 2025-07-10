# Final Production Code for drive_gpt_app.py

import os
import streamlit as st
import json
import openai
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
# ... (all other imports are the same) ...
import io, base64, fitz, docx, pptx
from PIL import Image
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload


# --- Configuration ---
st.set_page_config(page_title="File Analyzer", page_icon="🧩")
st.title("🧩 Google Drive File Analyzer")
st.write("Select one or more files from Google Drive for analysis.")

# --- Constants ---
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
# NOTE: The redirect URI for deployment will be your app's URL.
# For local testing, it's localhost. We will use the deployed URL later.
REDIRECT_URI = "https://zw2bm6uwryon2f5pnfsauk.streamlit.app/" 

# --- Helper Functions (No changes here) ---
def get_file_content(drive_service, file_info):
    # ... (this function is unchanged from the last working version)
    file_id = file_info['id']
    mime_type = file_info['mimeType']
    try:
        request = drive_service.files().get_media(fileId=file_id)
        file_bytes = io.BytesIO()
        downloader = MediaIoBaseDownload(file_bytes, request)
        done = False
        while not done: status, done = downloader.next_chunk()
        file_bytes.seek(0)
        if 'pdf' in mime_type:
            with fitz.open(stream=file_bytes, filetype="pdf") as doc: return "text", "".join(page.get_text() for page in doc)
        elif 'vnd.openxmlformats-officedocument.wordprocessingml.document' in mime_type:
            doc = docx.Document(file_bytes); return "text", "\n".join([para.text for para in doc.paragraphs])
        elif 'vnd.openxmlformats-officedocument.presentationml.presentation' in mime_type:
            prs = pptx.Presentation(file_bytes); text_runs = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if not shape.has_text_frame: continue
                    for paragraph in shape.text_frame.paragraphs:
                        for run in paragraph.runs: text_runs.append(run.text)
            return "text", "\n".join(text_runs)
        elif 'image' in mime_type:
            Image.open(file_bytes); return "image", file_bytes.getvalue()
        elif 'text' in mime_type:
            return "text", file_bytes.getvalue().decode("utf-8", errors='ignore')
        else:
            return "unsupported", f"File type ('{mime_type}')"
    except HttpError:
        if 'google-apps' in mime_type:
            if mime_type == 'application/vnd.google-apps.shortcut': return "unsupported", "Google Drive Shortcut"
            export_mime_type = None
            if mime_type == 'application/vnd.google-apps.document': export_mime_type = 'text/plain'
            elif mime_type == 'application/vnd.google-apps.spreadsheet': export_mime_type = 'text/csv'
            elif mime_type == 'application/vnd.google-apps.presentation': export_mime_type = 'text/plain'
            if export_mime_type:
                try:
                    request = drive_service.files().export_media(fileId=file_id, mimeType=export_mime_type); file_bytes = io.BytesIO()
                    downloader = MediaIoBaseDownload(file_bytes, request); done = False
                    while not done: status, done = downloader.next_chunk()
                    return "text", file_bytes.getvalue().decode("utf-8")
                except HttpError as e: return "unsupported", f"Export Error: {e}"
            else: return "unsupported", f"Google Workspace type ({mime_type})"
        else: return "unsupported", "Google Drive API Error"

def get_ai_response(api_key, text_parts, image_parts, user_prompt):
    # ... (this function is unchanged)
    try:
        client = openai.OpenAI(api_key=api_key)
        message_content = [{"type": "text", "text": user_prompt}]
        if text_parts:
            combined_text = "\n\n".join(text_parts)
            message_content.append({"type": "text", "text": combined_text})
        for image_bytes in image_parts:
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            message_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}})
        messages = [{"role": "user", "content": message_content}]
        response = client.chat.completions.create(model="gpt-4o", messages=messages, max_tokens=4000)
        return response.choices[0].message.content
    except Exception as e:
        return f"ERROR: {str(e)}"

# --- Main App Logic ---
if "credentials" not in st.session_state:
    st.session_state.credentials = None

if st.session_state.credentials is None:
    st.subheader("Step 1: Authenticate with Google")
    try:
        # UPDATED: Load credentials from st.secrets instead of a file
        client_config = st.secrets["google_credentials"]
        flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI)
        
        auth_url, _ = flow.authorization_url(prompt="consent")
        st.link_button("Login with Google", auth_url)
        st.write("After authorizing, copy the full redirected URL and paste it below.")
        redirected_url = st.text_input("Paste the full redirected URL here:")
        if redirected_url:
            code = redirected_url.split('code=')[1].split('&scope')[0]
            flow.fetch_token(code=code)
            creds = flow.credentials
            st.session_state.credentials = creds.to_json()
            st.rerun()
    except Exception as e:
        st.error("Could not load Google credentials from secrets. Have you set them up for deployment?")
        st.error(f"Specific error: {e}")
else:
    # ... (The rest of the code is unchanged) ...
    creds = Credentials.from_authorized_user_info(json.loads(st.session_state.credentials))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request()); st.session_state.credentials = creds.to_json()
    drive_service = build("drive", "v3", credentials=creds)
    st.success("✅ Connected to Google Drive!")
    if st.button("Logout"):
        st.session_state.credentials = None; st.rerun()
    st.divider()

    st.subheader("Step 2: Select Files and Ask a Question")
    try:
        results = drive_service.files().list(pageSize=200, fields="files(id, name, mimeType)", q="mimeType != 'application/vnd.google-apps.folder'").execute()
        files = results.get("files", [])
        if not files:
            st.write("No files found in your Google Drive.")
        else:
            file_options = {f"{file['name']} ({file['mimeType']})": file for file in files}
            selected_files_display = st.multiselect("Choose up to 30 files to analyze:", options=list(file_options.keys()), max_selections=30)
            
            user_prompt = st.text_area("What would you like to know about these files?", height=100)

            if st.button("✨ Analyze Files", disabled=(not selected_files_display or not user_prompt)):
                text_parts, image_parts = [], []
                with st.spinner("Processing files..."):
                    for file_display in selected_files_display:
                        file_info = file_options[file_display]
                        content_type, content = get_file_content(drive_service, file_info)
                        if content_type == 'text':
                            text_parts.append(f"--- DOCUMENT: {file_info['name']} ---\n{content}")
                        elif content_type == 'image':
                            image_parts.append(content)
                        else:
                            st.warning(f"Skipping unsupported file: {file_info['name']} ({content})")
                
                if text_parts or image_parts:
                    st.info(f"Sending {len(text_parts)} document(s) and {len(image_parts)} image(s) for analysis.")
                    with st.spinner("🤖 AI is thinking..."):
                        api_key = st.secrets["openai"]["api_key"]
                        response = get_ai_response(api_key, text_parts, image_parts, user_prompt)
                        st.markdown("### 🤖 AI Analysis")
                        st.markdown(response)

    except HttpError as error:
        st.error(f"A Google Drive API error occurred: {error}")
