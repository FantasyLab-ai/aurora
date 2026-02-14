#!/usr/bin/env python3
import os
import subprocess
from datetime import datetime

def create_fallback_video(prompt, output_dir):
    """
    Create a simple fallback video when Veo is not available
    """
    # Create a text file with the prompt
    text_file = os.path.join(output_dir, "prompt_text.txt")
    with open(text_file, "w") as f:
        f.write(prompt[:100] + "...")  # Limit text length
    
    # Create a simple video with text overlay
    output_path = os.path.join(output_dir, f"fallback_video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4")
    
    # Use FFmpeg to create a video with text
    cmd = [
        'ffmpeg',
        '-f', 'lavfi',
        '-i', 'color=c=blue:s=1280x720:d=8',  # 8-second blue background
        '-vf', f"drawtext=textfile={text_file}:fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:fontsize=24:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2",
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '23',
        '-y',  # Overwrite output file
        output_path
    ]
    
    try:
        subprocess.run(cmd, check=True)
        return output_path
    except subprocess.CalledProcessError as e:
        print(f"Fallback video creation failed: {e}")
        return None

if __name__ == "__main__":
    # Test the fallback function
    test_prompt = "This is a test prompt for fallback video generation"
    output_dir = os.path.expanduser("~/FantasyAI/outputs")
    os.makedirs(output_dir, exist_ok=True)
    video_path = create_fallback_video(test_prompt, output_dir)
    print(f"Fallback video created: {video_path}")
