#!/usr/bin/env python3
import os
import json
from datetime import datetime

def manual_workflow():
    """Manual workflow for TikTok application demonstration"""
    print("🎬 FantasyLab.ai Pipeline - Manual Workflow")
    print("=" * 50)
    
    # Step 1: Show trend analysis
    from pipeline import fetch_trends, cluster_trends, generate_prompt
    trends = fetch_trends()
    selected_trend = cluster_trends(trends)
    print(f"📊 Selected Trend: {selected_trend}")
    
    # Step 2: Show prompt generation
    prompt = generate_prompt(selected_trend)
    print(f"📝 Generated Prompt:\n{prompt}")
    
    # Save prompt to file
    prompt_file = os.path.expanduser("~/FantasyAI/veo_prompt.txt")
    with open(prompt_file, "w") as f:
        f.write(prompt)
    print(f"💾 Prompt saved to: {prompt_file}")
    
    # Step 3: Instructions for manual video creation
    print("\n🎥 Manual Video Creation Instructions:")
    print("1. Open https://veo.google.com in your browser")
    print("2. Make sure you're logged into your Google account")
    print("3. Copy the prompt from the file above")
    print("4. Paste it into Google Veo and generate the video")
    print("5. Download the video and save it to ~/FantasyAI/outputs/")
    print("6. The pipeline will handle editing and uploading")
    
    # Step 4: Once video is created, continue with the pipeline
    input("\nPress Enter after you've created and downloaded the video...")
    
    # Continue with editing and uploading
    from pipeline import edit_with_capcut, upload_to_tiktok, upload_to_youtube
    from pipeline import get_latest_file, convert_to_mp4
    
    video_path = get_latest_file(os.path.expanduser("~/FantasyAI/outputs"), ".mp4")
    if video_path:
        print(f"📹 Found video: {video_path}")
        
        # Ensure MP4 format
        if not video_path.endswith('.mp4'):
            video_path = convert_to_mp4(video_path)
            
        # Edit with CapCut
        final_video = edit_with_capcut(video_path)
        
        # Upload to platforms
        upload_to_tiktok(final_video)
        upload_to_youtube(final_video)
        
        print("✅ Manual workflow completed successfully!")
    else:
        print("❌ No video found in outputs directory")

if __name__ == "__main__":
    manual_workflow()
