#!/usr/bin/env python3
import os, json, time, base64, subprocess, shlex, sys
import boto3, requests
from datetime import datetime

# ========================== READ ENV ==========================
generate_video_id = os.environ.get("GENERATE_VIDEO_ID", "gv_unknown")
target_duration   = int(os.environ.get("TARGET_DURATION", "10"))
upload_base_path  = os.environ.get("UPLOAD_BASE_PATH", f"video/{datetime.now().strftime('%Y/%m/%d')}/unknown")

s3_endpoint       = os.environ.get("S3_ENDPOINT", "")
s3_bucket         = os.environ.get("S3_BUCKET", "")
s3_access_key     = os.environ.get("S3_ACCESS_KEY", "")
s3_secret_key     = os.environ.get("S3_SECRET_KEY", "")
public_base_url   = os.environ.get("PUBLIC_BASE_URL", "")

callback_url      = os.environ.get("CALLBACK_URL", "")
callback_api_key  = os.environ.get("CALLBACK_API_KEY", "")

project_dir       = os.environ.get("PROJECT_DIR", "/root/project")
wan_task          = os.environ.get("WAN_TASK", "t2v-1.3B")
wan_size          = os.environ.get("WAN_SIZE", "832*480")
ckpt_dir          = os.environ.get("CKPT_DIR", "/root/models/Wan2.1-T2V-1.3B")

prompts_b64       = os.environ.get("PROMPTS_B64", "W10=")
try:
    prompts       = json.loads(base64.b64decode(prompts_b64).decode("utf-8"))
except Exception as e:
    print("[ERROR] Failed to decode PROMPTS_B64:", e)
    prompts = []

# ========================== INIT S3 ==========================
s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{s3_endpoint}",
    aws_access_key_id=s3_access_key,
    aws_secret_access_key=s3_secret_key,
)

date_path = datetime.now().strftime("%Y/%m/%d")
if not upload_base_path:
    upload_base_path = f"video/{date_path}/{generate_video_id}"

video_urls = []

# ========================== HELPERS ==========================
def send_callback(endpoint, payload):
    if not callback_url:
        return
    url = f"{callback_url}/{generate_video_id}/{endpoint}"
    headers = {"Authorization": f"Bearer {callback_api_key}"} if callback_api_key else {}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        print(f"[CALLBACK:{endpoint}] {r.status_code} {r.text}")
    except Exception as e:
        print(f"[ERROR] Callback {endpoint} failed:", e)

def ensure_duration(in_path, out_path, target_sec):
    try:
        probe = subprocess.run(
            ["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1", in_path],
            capture_output=True, text=True
        )
        orig = float(probe.stdout.strip()) if probe.returncode == 0 else 0.0
    except:
        orig = 0.0

    if orig <= 0.1:
        subprocess.run(["ffmpeg","-y","-i", in_path, "-t", str(target_sec), "-c","copy", out_path], check=False)
        return

    if orig > target_sec + 0.1:
        subprocess.run(["ffmpeg","-y","-i", in_path, "-t", str(target_sec), "-c","copy", out_path], check=False)
    elif orig < target_sec - 0.1:
        loop_count = max(1, int(target_sec // max(orig,1)))
        subprocess.run(
            ["ffmpeg","-y","-stream_loop", str(loop_count), "-i", in_path, "-t", str(target_sec), "-c","libx264","-pix_fmt","yuv420p", out_path],
            check=False
        )
    else:
        subprocess.run(["cp", in_path, out_path], check=False)

# ========================== MAIN ==========================
try:
    for idx, prompt in enumerate(prompts):
        print(f"[INFO] ({idx+1}/{len(prompts)}) Generating: {prompt}")
        tmp_out   = f"/tmp/{generate_video_id}_{idx}.mp4"
        final_out = f"/tmp/{generate_video_id}_{idx}_final.mp4"

        try:
            # --- PANGGIL generate.py WAN2.1 ---
            cmd = f"python3 generate.py --task {wan_task} --size {wan_size} --ckpt_dir {shlex.quote(ckpt_dir)} --prompt {shlex.quote(prompt)}"
            subprocess.run(cmd, cwd=project_dir, shell=True, check=True)
            produced = os.path.join(project_dir, "output.mp4")
            if not os.path.exists(produced):
                produced = tmp_out
                open(produced, "wb").write(b"DUMMY")
        except Exception as e:
            print("[ERROR] generate.py failed:", e)
            produced = tmp_out
            open(produced, "wb").write(b"DUMMY")

        # --- PAKSA DURASI SESUAI TARGET ---
        ensure_duration(produced, final_out, target_duration)

        # --- UPLOAD KE S3 ---
        s3_key = f"{upload_base_path}/{idx}.mp4"
        video_url = None
        try:
            s3.upload_file(final_out, s3_bucket, s3_key)
            video_url = f"{public_base_url}/{s3_key}"
            video_urls.append(video_url)
            print(f"[INFO] Uploaded: {video_url}")
        except Exception as e:
            print("[ERROR] Upload failed:", e)

        # --- CALLBACK PROGRESS ---
        send_callback("progress", {
            "status": f"GENERATING {idx+1}/{len(prompts)}",
            "current_index": idx,
            "current_url": video_url,
            "video_urls": video_urls,
        })

        time.sleep(3)

    # ========================== CALLBACK SUCCESS ==========================
    send_callback("success", {
        "action": "start",
        "status": "GENERATING",
        "video_urls": video_urls
    })

except Exception as e:
    # ========================== CALLBACK FAIL ==========================
    print("[FATAL] Pipeline failed:", e)
    send_callback("fail", {
        "status": "FAILED",
        "failed_reason": str(e),
        "video_urls": video_urls
    })
    sys.exit(1)

print("[DONE] All videos processed.")
