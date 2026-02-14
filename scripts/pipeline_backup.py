#!/usr/bin/env python3

import os
import json
import time
import logging
import requests
import subprocess
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from pyvirtualdisplay import Display
import pyautogui
from fallback_video import create_fallback_video

# === CONFIGURATION ===
BASE_DIR = os.path.expanduser("~/FantasyAI")
SCRIPT_DIR = os.path.join(BASE_DIR, "scripts")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
os.makedirs(OUTPUT_DIR, exist_ok=True)

OLLAMA_URL = "http://localhost:11434/api/generate"
GOOGLE_VEO_URL = "https://veo.google/"
CAPCUT_TEMPLATE_ID = "YOUR_CAPCUT_TEMPLATE_ID"
SERVICE_ACCOUNT_FILE = os.path.join(SCRIPT_DIR, "service-account.json")
TIKTOK_CREDS_FILE = os.path.join(SCRIPT_DIR, "tiktok_creds.json")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, "pipeline.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# === UTILITY FUNCTIONS ===
def convert_to_mp4(input_path, output_path=None):
    """
    Convert any video format to MP4 using FFmpeg
    """
    if output_path is None:
        output_path = os.path.splitext(input_path)[0] + ".mp4"
    
    try:
        # FFmpeg command to convert to MP4 with H.264 codec (TikTok compatible)
        cmd = [
            'ffmpeg', '-i', input_path,
            '-c:v', 'libx264',       # H.264 video codec
            '-preset', 'fast',       # Balance between speed and compression
            '-crf', '23',            # Quality level (0-51, lower is better)
            '-c:a', 'aac',           # AAC audio codec
            '-b:a', '128k',          # Audio bitrate
            '-movflags', '+faststart', # Enable streaming
            '-y',                    # Overwrite output file if exists
            output_path
        ]
        
        subprocess.run(cmd, check=True)
        
        # Remove original file if conversion successful
        if input_path != output_path and os.path.exists(output_path):
            os.remove(input_path)
            
        return output_path
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Video conversion failed: {e}")
        # Fallback: just rename if already MP4 but wrong extension
        if input_path.endswith('.mp4'):
            os.rename(input_path, output_path)
            return output_path
        return input_path

def validate_video_for_tiktok(video_path):
    """
    Check if video meets TikTok's requirements
    """
    try:
        # Use FFprobe to get video info
        cmd = [
            'ffprobe', 
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            '-show_streams',
            video_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        video_info = json.loads(result.stdout)
        
        # Check requirements
        requirements = {
            'size': float(video_info['format']['size']) < 287.6 * 1024 * 1024,  # Convert MB to bytes
            'duration': 5 <= float(video_info['format']['duration']) <= 600,  # 5 sec to 10 min
        }
        
        # Check video stream
        video_stream = next((stream for stream in video_info['streams'] if stream['codec_type'] == 'video'), None)
        if video_stream:
            requirements['video_codec'] = video_stream['codec_name'] == 'h264'
            width = int(video_stream['width'])
            height = int(video_stream['height'])
            requirements['aspect_ratio'] = (width / height) in [9/16, 1/1, 16/9]
        
        # Check if all requirements are met
        if all(requirements.values()):
            return True, "Video meets TikTok requirements"
        else:
            # Return which requirements failed
            failed = [req for req, met in requirements.items() if not met]
            return False, f"Video failed requirements: {failed}"
            
    except Exception as e:
        return False, f"Error checking video: {str(e)}"
# === TREND ANALYSIS ===
def fetch_trends():
    logger.info("📊 Fetching trends...")
    try:
        from real_trends import fetch_real_trends
        return fetch_real_trends()
    except Exception as e:
        logger.error(f"Real trends fetch failed: {e}, using mock trends")
        return [
            "Dancing Videos", "TikTok Duets", "ASMR", 
            "AI Art Challenges", "Viral Animal Videos"
def cluster_trends(trends):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans
    import random
    import numpy as np
    
    logger.info("🧠 Clustering trends...")
    
    # Track which trends have been used recently
    recent_trends = load_recent_trends()
    
    # Filter out recently used trends
    available_trends = [t for t in trends if t not in recent_trends]
    
    if not available_trends:
        available_trends = trends  # Fallback if all trends are recent
    
    vectorizer = TfidfVectorizer(stop_words='english')
    X = vectorizer.fit_transform(available_trends)
    
    # Determine optimal number of clusters
    n_clusters = min(3, len(available_trends))
    if n_clusters == 0:
        return random.choice(trends)
    
    kmeans = KMeans(n_clusters=n_clusters, random_state=0, n_init=10)
    kmeans.fit(X)
    
    # Get cluster sizes and select one proportionally
    cluster_sizes = [sum(kmeans.labels_ == i) for i in range(n_clusters)]
    total = sum(cluster_sizes)
    weights = [size/total for size in cluster_sizes]
    
    # Select a cluster weighted by size
    selected_cluster = random.choices(range(n_clusters), weights=weights, k=1)[0]
    
    # Get trends from selected cluster
    cluster_trends = [available_trends[i] for i in range(len(available_trends)) if kmeans.labels_[i] == selected_cluster]
    
    # Randomly select a trend from the cluster
    selected_trend = random.choice(cluster_trends)
    
    # Update recent trends
    update_recent_trends(selected_trend)
    
    return selected_trend

def load_recent_trends():
    """Load recently used trends from file"""
    recent_file = os.path.join(BASE_DIR, "recent_trends.json")
    if os.path.exists(recent_file):
        with open(recent_file, 'r') as f:
            return json.load(f)
    return []

def update_recent_trends(new_trend):
    """Update the list of recently used trends"""
    recent_file = os.path.join(BASE_DIR, "recent_trends.json")
    recent_trends = load_recent_trends()
    
    # Add new trend and keep only last 10
    recent_trends.append(new_trend)
    recent_trends = recent_trends[-10:]
    
    with open(recent_file, 'w') as f:
        json.dump(recent_trends, f)        ]
# === PROMPT GENERATION ===
def generate_prompt(trend):
    logger.info(f"🎬 Generating prompt for: {trend}")
    payload = {
        "model": "mistral",
        "prompt": f"Create an 8-second viral TikTok video about {trend}. Include: "
                  "1. Vertical format (9:16) 2. Trending audio 3. Text overlays "
                  "4. Quick cuts 5. Surprise ending",
        "stream": False
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload)
        response.raise_for_status()
        return response.json()["response"].strip()
    except Exception as e:
        logger.error(f"Prompt generation failed: {e}")
        raise

# === VIDEO GENERATION ===
def generate_veo_video(prompt):
    logger.info("🎥 Generating video with Google Veo...")
    
    # Use your existing browser session (no virtual display)
    options = webdriver.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # Use Chromium
    options.binary_location = "/usr/bin/chromium"
    
    # Connect to your existing browser session
    # First, make sure you have Chrome Remote Debugging enabled
    options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
    
    try:
        driver = webdriver.Chrome(
            service=Service("/usr/bin/chromedriver"),
            options=options
        )
        
        driver.get(GOOGLE_VEO_URL)
        
        # Wait for page to load
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.TAG_NAME, "textarea"))
        )
        
        # Enter prompt
        textarea = driver.find_element(By.TAG_NAME, "textarea")
        textarea.clear()
        textarea.send_keys(prompt)
        
        # Generate video
        generate_btn = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Create video')]"))
        )
        generate_btn.click()
        
        # Wait for generation to complete
        WebDriverWait(driver, 300).until(
            EC.presence_of_element_located((By.XPATH, "//button[contains(., 'Download')]"))
        )
        
        # Download video
        download_btn = driver.find_element(By.XPATH, "//button[contains(., 'Download')]")
        download_btn.click()
        
        # Wait for download to complete
        time.sleep(10)
        video_path = get_latest_file(OUTPUT_DIR, ".mp4")
        
        # Ensure MP4 format
        if video_path and not video_path.endswith('.mp4'):
            video_path = convert_to_mp4(video_path)
            
        return video_path
        
    except Exception as e:
        logger.error(f"Google Veo generation failed: {e}")
        # Fallback to a simple video creation method
        return create_fallback_video(prompt, OUTPUT_DIR)
    finally:
        if 'driver' in locals():
            driver.quit()
def get_latest_file(directory, extension):
    files = [f for f in os.listdir(directory) if f.endswith(extension)]
    paths = [os.path.join(directory, f) for f in files]
    return max(paths, key=os.path.getctime) if paths else None

# === VIDEO EDITING ===
def edit_with_capcut(video_path):
    logger.info("✂️ Editing video with CapCut...")
    
    # Start virtual display for GUI automation
    display = Display(visible=0, size=(1920, 1080))
    display.start()
    
    try:
        # Open CapCut template
        os.system(f"xdg-open 'capcut://template/detail?template_id={CAPCUT_TEMPLATE_ID}'")
        time.sleep(10)
        
        # Automation sequence (calibrate these coordinates)
        pyautogui.hotkey('ctrl', 'i')  # Import shortcut
        time.sleep(2)
        pyautogui.write(video_path)
        pyautogui.press('enter')
        time.sleep(5)
        
        # Auto-edit workflow
        pyautogui.click(x=1200, y=650)  # Apply template button
        time.sleep(3)
        pyautogui.click(x=1350, y=800)  # Export button
        time.sleep(2)
        
        # Set output filename
        output_path = os.path.join(OUTPUT_DIR, "final_video.mp4")
        pyautogui.write(output_path)
        pyautogui.press('enter')
        
        # Wait for export
        time.sleep(30)
        
        # Ensure MP4 format
        if not output_path.endswith('.mp4'):
            output_path = convert_to_mp4(output_path)
            
        return output_path
        
    finally:
        display.stop()

# === SOCIAL MEDIA UPLOAD ===
def upload_to_tiktok(video_path):
    logger.info("🆙 Uploading to TikTok...")
    
    # Ensure MP4 format
    if not video_path.endswith('.mp4'):
        video_path = convert_to_mp4(video_path)
    
    # Validate video
    is_valid, message = validate_video_for_tiktok(video_path)
    if not is_valid:
        logger.error(f"❌ TikTok upload failed validation: {message}")
        return False
    
    try:
        creds = json.load(open(TIKTOK_CREDS_FILE))
        
        # TikTok upload API (simplified)
        upload_url = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
        headers = {
            "Authorization": f"Bearer {creds['access_token']}",
            "Content-Type": "application/json"
        }
        payload = {
            "post_info": {
                "title": "AI Generated Video",
                "privacy_level": "PUBLIC",
                "disable_comment": False,
                "disable_duet": False,
                "disable_stitch": False
            }
        }
        
        response = requests.post(upload_url, headers=headers, json=payload)
        response.raise_for_status()
        logger.info(f"✅ TikTok upload successful! ID: {response.json()['data']['publish_id']}")
        return True
    except Exception as e:
        logger.error(f"❌ TikTok upload failed: {str(e)}")
        return False

def upload_to_youtube(video_path):
    logger.info("🆙 Uploading to YouTube...")
    
    # Ensure MP4 format
    if not video_path.endswith('.mp4'):
        video_path = convert_to_mp4(video_path)
    
    try:
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=['https://www.googleapis.com/auth/youtube.upload']
        )
        youtube = build('youtube', 'v3', credentials=credentials)
        
        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": "AI Generated Video",
                    "description": "Created with FantasyAI pipeline",
                    "tags": ["AI", "Generated", "Veo"],
                    "categoryId": "22"
                },
                "status": {"privacyStatus": "public"}
            },
            media_body=MediaFileUpload(video_path)
        )
        response = request.execute()
        logger.info(f"✅ YouTube upload successful! ID: {response['id']}")
        return True
    except Exception as e:
        logger.error(f"❌ YouTube upload failed: {str(e)}")
        return False

# === MAIN WORKFLOW ===
def main():
    try:
        # Step 1: Get trends
        trends = fetch_trends()
        selected_trend = cluster_trends(trends)
        
        # Step 2: Generate prompt
        prompt = generate_prompt(selected_trend)
        logger.info(f"📝 Generated Prompt:\n{prompt}")
        
        # Step 3: Create video
        raw_video = generate_veo_video(prompt)
        if not raw_video:
            raise Exception("Video generation failed")
        
        # Step 4: Edit video
        final_video = edit_with_capcut(raw_video)
        
        # Step 5: Upload to platforms
        upload_to_tiktok(final_video)
        upload_to_youtube(final_video)
        
        logger.info("🚀 Pipeline completed successfully!")
        
    except Exception as e:
        logger.error(f"🔥 Pipeline failed: {str(e)}")

if __name__ == "__main__":
    main()
