import re
import time
import logging
import requests
import os
from concurrent.futures import ThreadPoolExecutor

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def download_file(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logging.error(f"下载文件失败: {e}")
        return None

def extract_urls(text):
    pattern = re.compile(
        r'http://(?:(?:\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})|(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,})(?::\d{1,4})(?=/|$)',
        re.IGNORECASE
    )
    return pattern.findall(text)

def check_video_speed(url, timeout=3, duration=3, min_speed=0.1):
    try:
        start_time = time.time()
        response = requests.get(url, stream=True, timeout=timeout)
        response.raise_for_status()

        downloaded_size = 0
        for chunk in response.iter_content(chunk_size=1024):
            if chunk:
                downloaded_size += len(chunk)
                if time.time() - start_time >= duration:
                    break

        elapsed_time = time.time() - start_time
        if elapsed_time == 0:
            return None

        speed = (downloaded_size / 1024) / elapsed_time  # KB/s
        if speed >= min_speed:
            return url.rstrip('/')
        else:
            logging.warning(f"无效地址: {url} (网速: {speed:.2f} KB/s)")
    except requests.RequestException as e:
        logging.error(f"请求失败: {url} (错误: {e})")
    return None

def extract_base_url(url):
    pattern = re.compile(r"(http://[^/]+)")
    match = pattern.match(url)
    return match.group(1) if match else None

def validate_urls(urls, num_threads=20):
    valid_urls = []
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        future_url_dict = {}
        for url in urls:
            future = executor.submit(check_video_speed, url)
            future_url_dict[future] = url

        for future in future_url_dict:
            result = future.result()
            if result:
                valid_urls.append(result)
            else:
                url = future_url_dict[future]
                base_url = extract_base_url(url)
                if base_url:
                    for i in range(1, 21):
                        new_url = f"{base_url}/hls/{i}/index.m3u8"
                        new_result = check_video_speed(new_url)
                        if new_result:
                            valid_urls.append(new_result)
                            break
    return valid_urls

def process_line(index, base_url):
    results = []
    current_start = 1
    current_end = 100
    max_i = 200

    while True:
        for i in range(current_start, current_end + 1):
            new_index = f"{index}A{i}"
            new_url = f"{base_url}/hls/{i}/index.m3u8"
            result = check_video_speed(new_url)
            if result:
                results.append(f"{new_index},{result}")

        # 检查最后5个i是否有有效地址
        last_five_start = max(current_start, current_end - 4)
        has_valid = any(
            any(entry.startswith(f"{index}A{i}") for entry in results)
            for i in range(last_five_start, current_end + 1)
        )

        if has_valid and current_end < max_i:
            next_start = current_end + 1
            next_end = min(current_end + 20, max_i)
            current_start, current_end = next_start, next_end
        else:
            break

    return results

def save_urls_to_file(urls, filename):
    with open(filename, "w") as file:
        for index, url in enumerate(urls, start=1):
            match = re.match(r'http://([^/]+)', url)
            if match:
                base_url = match.group(0)
                processed_lines = process_line(index, base_url)
                for line in processed_lines:
                    file.write(line + "\n")
    logging.info(f"已保存 {len(urls)} 个有效地址到 {filename}")

def main():
    file_url = "https://10694.kstore.space/hlstvid.txt"
    output_file = "h1.txt"
    final_output_file = "h2.txt"

    logging.info(f"当前工作目录: {os.getcwd()}")

    if (content := download_file(file_url)) is not None:
        raw_urls = list(set(extract_urls(content)))
        logging.info(f"发现 {len(raw_urls)} 个待验证地址")

        valid_urls = validate_urls(raw_urls)

        if valid_urls:
            save_urls_to_file(valid_urls, output_file)
        else:
            logging.warning("未找到有效地址")

    try:
        logging.info(f"正在处理生成的新地址并保存到 {final_output_file}")
        with open(output_file, "r") as infile, open(final_output_file, "w") as outfile:
            for line in infile:
                line = line.strip()
                if not line:
                    continue
                match = re.match(r"(\d+),http://(.+?)/hls/\d+/index\.m3u8", line)
                if match:
                    idx, base = match.groups()
                    base_url = f"http://{base}"
                    processed = process_line(idx, base_url)
                    for entry in processed:
                        outfile.write(entry + "\n")
        logging.info(f"处理完成，结果已保存到 {final_output_file}")
    except Exception as e:
        logging.error(f"处理文件时出错: {e}")

if __name__ == "__main__":
    main()

