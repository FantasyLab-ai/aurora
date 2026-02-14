#!/usr/bin/env python3
import os
import subprocess
import json

def convert_to_mp4(input_path, output_path=None):
    """
    Convert any video format to MP4 using FFmpeg
    """
    if output_path is None:
        output_path = os.path.splitext(input_path)[0] + ".mp4"
    
    try:
        cmd = [
            'ffmpeg', '-i', input_path,
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', '-y', output_path
        ]
        
        subprocess.run(cmd, check=True)
        
        if input_path != output_path and os.path.exists(output_path):
            os.remove(input_path)
            
        return output_path
        
    except subprocess.CalledProcessError as e:
        print(f"Conversion failed: {e}")
        if input_path.endswith('.mp4'):
            os.rename(input_path, output_path)
            return output_path
        return input_path

def validate_video_for_tiktok(video_path):
    """
    Check if video meets TikTok's requirements
    """
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', video_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        video_info = json.loads(result.stdout)
        
        requirements = {
            'format': video_info['format']['format_name'] == 'mp4',
            'size': float(video_info['format']['size']) < 287.6 * 1024 * 1024,
            'duration': 5 <= float(video_info['format']['duration']) <= 600,
        }
        
        video_stream = next((stream for stream in video_info['streams'] if stream['codec_type'] == 'video'), None)
        if video_stream:
            requirements['video_codec'] = video_stream['codec_name'] == 'h264'
            width = int(video_stream['width'])
            height = int(video_stream['height'])
            requirements['aspect_ratio'] = (width / height) in [9/16, 1/1, 16/9]
        
        if all(requirements.values()):
            return True, "Video meets TikTok requirements"
        else:
            failed = [req for req, met in requirements.items() if not met]
            return False, f"Video failed requirements: {failed}"
            
    except Exception as e:
        return False, f"Error checking video: {str(e)}"

# Test the functions
if __name__ == "__main__":
    # Create test video
    os.system('ffmpeg -f lavfi -i testsrc=duration=5:size=1280x720:rate=30 test_video.mov')
    
    # Test conversion
    result = convert_to_mp4('test_video.mov')
    print(f'Converted to: {result}')
    
    # Test validation
    is_valid, message = validate_video_for_tiktok('test_video.mp4')
    print(f'Valid: {is_valid}, Message: {message}')
