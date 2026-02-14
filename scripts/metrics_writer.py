import csv, os, time, pathlib
METRICS_PATH = pathlib.Path(os.getenv("BASE_DIR","/mnt/c/Users/bgrut/Desktop/FantasyAI")) / "data" / "metrics.csv"
METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)

def write_metric(**row):
    row.setdefault("ts", int(time.time()))
    row.setdefault("provider", os.getenv("LLM_PROVIDER","unknown"))
    header = ["ts","trend","pair","score","provider","len_chars","source_kw","fact_hit","video_path","notes"]
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_file = not METRICS_PATH.exists()
    with METRICS_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        if new_file: w.writeheader()
        w.writerow({k: row.get(k,"") for k in header})
