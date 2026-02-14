# Copy and paste this into a text editor, then save as edit_script.sh
#!/bin/bash

# Simple edit script for your files
# Usage: ./edit_script.sh

# Create backup first
echo "Creating backup files..."
cp config.json config.json.backup 2>/dev/null || echo "config.json not found"
cp app.js app.js.backup 2>/dev/null || echo "app.js not found"
cp package.json package.json.backup 2>/dev/null || echo "package.json not found"

# Apply your edits using sed
echo "Applying edits..."

# Edit config.json
sed -i 's/"debug": false/"debug": true/g' config.json 2>/dev/null || echo "config.json edit failed"
sed -i 's/"port": 3000/"port": 8080/g' config.json 2>/dev/null || echo "config.json port edit failed"

# Edit app.js
sed -i 's/localhost/0.0.0.0/g' app.js 2>/dev/null || echo "app.js edit failed"
sed -i 's/3000/8080/g' app.js 2>/dev/null || echo "app.js port edit failed"

# Edit package.json
sed -i 's/"version": "1.0.0"/"version": "1.0.1"/g' package.json 2>/dev/null || echo "package.json version edit failed"
sed -i 's/"start": "node app.js"/"start": "node app.js --port 8080"/g' package.json 2>/dev/null || echo "package.json start edit failed"

echo "Edits applied successfully! Backups created."
