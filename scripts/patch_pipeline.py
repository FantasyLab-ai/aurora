import re, shutil, sys, os
fn = os.path.expanduser("~/FantasyAI/scripts/pipeline.py")
bak = fn + ".bak.autopatch"
with open(fn, "r", encoding="utf-8") as f:
    src = f.read()

orig = src
# -----------------------------
# Helper: add the generate/run code inside FantasyAIPipeline.run()
# -----------------------------
generate_block = """
        if PRINT_PROMPT:
            logger.info("📝 Final prompt:\\n%s", final_prompt)

        if not VEO_USE_CDP:
            raise RuntimeError(
                "This build is CDP-only. Set VEO_USE_CDP=1 and run Chromium with --remote-debugging-port."
            )

        video_path = generate_veo_video_cdp(final_prompt)
        edited = edit_with_capcut(video_path)

        caption = self._caption(selected, prompt_json["character_set"])
        ok, msg = validate_video_for_tiktok(edited)
        if not ok:
            logger.warning("TikTok check: %s", msg)

        # DRY RUN unless POST_TIKTOK/POST_YOUTUBE=1
        self.social.schedule_tiktok(edited, caption)
        self.social.schedule_youtube(edited, caption)

        logger.info("✅ Pipeline finished.")
        return {"video_path": edited}
"""

# Try to remove the debug block that returns early.
pat_debug = re.compile(
    r"\n\s*#\s*Enhanced animal/creature focused trends[\s\S]*?return\s+enhanced_trends\s*\n",
    re.MULTILINE
)
src2, n_removed = pat_debug.subn("\n" + generate_block + "\n", src)

if n_removed == 0:
    # Fallback: insert after the candidate-save 'except ...: pass'
    anchor = re.search(
        r"logger\.info\(\"🧪 Saved %d prompt candidates → %s\",[^\n]*\)\n\s*except Exception:\n\s*pass",
        src
    )
    if not anchor:
        print("!! Could not find insertion point in run(); aborting to avoid damage.")
        sys.exit(1)
    ins_at = anchor.end()
    src2 = src[:ins_at] + generate_block + src[ins_at:]

src = src2

# -----------------------------
# Fix _fetch_enhanced_trends
# -----------------------------
pat_fetch = re.compile(
    r"(def\s+_fetch_enhanced_trends\(self\):[\s\S]*?)(?=\n\s*def\s|\n\s*class\s|\Z)",
    re.MULTILINE
)
replacement = (
    "def _fetch_enhanced_trends(self):\n"
    "        \"\"\"(Optional) Return up to 20 cached trends from TrendAnalyzer.\"\"\"\n"
    "        return (self.trend_analyzer.trend_data or {}).get(\"trends\", [])[:20]\n\n"
)
src3, n_fetch = pat_fetch.subn(replacement, src)
if n_fetch == 0:
    # If the method doesn't exist yet, just append it at the end of the class as a safe fallback.
    cls_match = re.search(r"class\s+FantasyAIPipeline\([\s\S]*?\):", src)
    if cls_match:
        # insert near the end of file (simple append)
        src3 = src + "\n    " + replacement
    else:
        src3 = src

# Only write if changed
if src3 == orig:
    print("No changes made (file already patched?).")
    sys.exit(0)

# Backup and write
shutil.copy2(fn, bak)
with open(fn, "w", encoding="utf-8") as f:
    f.write(src3)

print("Patched:", fn)
print("Backup  :", bak)
