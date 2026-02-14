#!/bin/bash
# Script to record a demo of the FantasyAI pipeline

# Install screen recorder if not already installed
sudo apt install -y ffmpeg

# Create a demo directory
DEMO_DIR="$HOME/FantasyAI/demo"
mkdir -p "$DEMO_DIR"

# Start recording the screen
echo "🎥 Starting screen recording in 5 seconds..."
sleep 5

# Record screen (adjust geometry to match your screen)
ffmpeg -video_size 1280x720 -framerate 30 -f x11grab -i :0.0+0,0 "$DEMO_DIR/demo_raw.mp4" &
RECORD_PID=$!

# Run the pipeline
cd "$HOME/FantasyAI"
source venv/bin/activate
python scripts/pipeline.py --test

# Stop recording
kill $RECORD_PID
wait $RECORD_PID

# Convert to a smaller file for upload
ffmpeg -i "$DEMO_DIR/demo_raw.mp4" -vf "scale=960:540" -c:v libx264 -crf 23 -preset fast "$DEMO_DIR/demo_final.mp4"

echo "✅ Demo recorded: $DEMO_DIR/demo_final.mp4"
