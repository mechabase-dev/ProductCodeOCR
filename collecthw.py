import csv
# import requests # requests の代わりに cloudscraper を使う
import cloudscraper # cloudscraper をインポート
import time
import os
import logging
from datetime import datetime
import json # JSONDecodeErrorのためにインポート

# ロギングの設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger()

# CSVファイルが格納されているフォルダのパス
csv_folder = './test/'

# --- cloudscraper インスタンスを作成 ---
# シンプルな作成方法。内部でセッションを管理し、ヘッダーも調整します。
scraper = cloudscraper.create_scraper(delay=10) # delayはCloudflareチェック間の最低待機時間(秒)
logger.info("Cloudscraper instance created.")

# --- User-AgentやRefererなどはcloudscraperに任せるのが基本だが、必要に応じて設定可能 ---
# scraper.headers.update({
#     "User-Agent": "...", # 特定のUAを使いたい場合
#     "Referer": "https://collecthw.com/",
# })

# --- 事前にトップページにアクセスしてセッション/チャレンジ解決試行 ---
initial_access_successful = False
try:
    logger.info("Attempting initial access to collecthw.com using cloudscraper...")
    # --- scraper.get を使用 ---
    initial_response = scraper.get("https://collecthw.com/", timeout=60) # timeoutを長めに設定 (チャレンジ解決に時間がかかる場合がある)
    initial_response.raise_for_status() # エラーチェック
    logger.info(f"Initial access successful. Status: {initial_response.status_code}")
    initial_access_successful = True
    # time.sleep(1) # 必要に応じて少し待機
except (requests.exceptions.RequestException, cloudscraper.exceptions.CloudflareException) as e:
    logger.error(f"Failed initial access to collecthw.com: {e}")
    if hasattr(e, 'response') and e.response is not None:
        logger.error(f"Response status: {e.response.status_code}")
        logger.error(f"Response text sample: {e.response.text[:500]}...") # エラーページの内容を確認
    # ここでスクリプトを終了させるか、処理を続行するか判断
    # logger.warning("Proceeding without successful initial access, API calls might fail.")
    # exit() # 失敗したら終了する場合はコメント解除

# APIリクエストを行う関数 (scraperを引数に追加)
def get_product_info(model_number, req_scraper):
    url = f"https://collecthw.com/find?query={model_number}"
    logger.info(f"Requesting data for model: {model_number}")

    try:
        # --- scraper.getを使用 ---
        # APIエンドポイント用のヘッダーは明示的に指定した方が良い場合が多い
        headers = {
            "Referer": "https://collecthw.com/",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest"
        }
        # scraperのデフォルトヘッダーとマージされる
        response = req_scraper.get(url, headers=headers, timeout=30) # APIリクエストのタイムアウト
        logger.info(f"Response status code: {response.status_code}")
        response.raise_for_status() # ここでHTTPエラーをチェック

        # --- JSONデコード処理 ---
        try:
            data = response.json()
            records_total = data.get('recordsTotal', '0')
            logger.info(f"Records found: {records_total}")

            if records_total != "0" and 'data' in data and len(data['data']) > 0:
                product = data['data'][0]
                model_name = product.get('ModelName', 'No name found')
                th_status = '★' if product.get('TH') == "1" else ''
                sth_status = '★' if product.get('STH') == "1" else ''
                logger.info(f"Retrieved: {model_name}, TH: {th_status}, STH: {sth_status}")
                return model_name, th_status, sth_status
            else:
                logger.warning(f"No data found for model: {model_number} in JSON response.")
                return "No name found", '', ''
        except json.JSONDecodeError as e: # requests.exceptions.JSONDecodeError の代わりに json を使う
            logger.error(f"JSON decode error for model {model_number}: {e} - Response text: {response.text[:200]}...")
            return "Error retrieving name (JSON Decode)", '', ''
        except IndexError:
             logger.error(f"IndexError: 'data' array might be empty for model {model_number}. JSON: {data}")
             return "Error retrieving name (Index)", '', ''

    # --- HTTPエラー処理 ---
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error for model {model_number}: {e}")
        if response.status_code == 403:
             logger.error("Received 403 Forbidden. Cloudflare/WAF might still be blocking.")
             logger.error(f"Response text (403): {response.text[:500]}...") # 403時のレスポンス内容を詳しく見る
        return f"Error: Status {response.status_code}", '', ''
    # --- Cloudflare関連エラー処理 ---
    except cloudscraper.exceptions.CloudflareException as e:
         logger.error(f"Cloudflare challenge failed for model {model_number}: {e}")
         return "Error: Cloudflare challenge", '', ''
    # --- その他のリクエスト関連エラー処理 ---
    except requests.exceptions.RequestException as e:
        logger.error(f"Request exception for model {model_number}: {e}")
        return f"Error: {str(e)}", '', ''
    except Exception as e:
        logger.error(f"An unexpected error occurred fetching data for {model_number}: {e}", exc_info=True)
        return "Error: Unexpected", '', ''


# 指定されたフォルダ内のすべてのCSVファイルを処理する (引数名 scraper に変更)
def process_all_csv_in_folder(csv_folder, req_scraper):
    logger.info(f"Starting to process CSV files in folder: {csv_folder}")

    if not os.path.exists(csv_folder):
        logger.error(f"Folder not found: {csv_folder}")
        return

    csv_files = [f for f in os.listdir(csv_folder) if f.endswith('.csv')]

    if not csv_files:
        logger.warning(f"No CSV files found in folder: {csv_folder}")
        return

    logger.info(f"Found {len(csv_files)} CSV files")

    for file_name in csv_files:
        csv_path = os.path.join(csv_folder, file_name)
        logger.info(f"Processing file: {file_name}")
        update_csv_with_names(csv_path, req_scraper) # scraper を渡す

# CSVを読み込み、名前、TH、STHを追加し、上書き保存する処理 (引数名 scraper に変更)
def update_csv_with_names(csv_path, req_scraper):
    logger.info(f"Reading CSV file: {csv_path}")
    rows = []
    fieldnames = []
    try:
        # CSVを読み込む (エンコーディングは utf-8-sig のまま)
        with open(csv_path, mode='r', encoding='utf-8-sig') as infile:
            reader = csv.DictReader(infile)
            if reader.fieldnames is None:
                 logger.error(f"Could not read header from {csv_path}. Is the file empty or corrupted?")
                 return
            fieldnames = reader.fieldnames[:]
            rows = list(reader)

        logger.info(f"Read {len(rows)} rows from {csv_path}")
        if not rows:
            logger.warning(f"CSV file {csv_path} is empty.")
            return

        # 型番から商品名、TH、STHステータスを取得
        for i, row in enumerate(rows):
            if row is None: continue
            if not isinstance(row, dict): continue

            model_number = row.get('model_number', '').strip()

            if not model_number:
                logger.warning(f"No model_number found or empty in row {i+1}")
                row['name'] = "Missing model number"
                row['TH'] = ''
                row['STH'] = ''
                continue

            logger.info(f"Processing row {i+1}/{len(rows)}: {model_number}")
            # --- scraperを渡してAPI呼び出し ---
            model_name, th_status, sth_status = get_product_info(model_number, req_scraper)
            row['name'] = model_name
            row['TH'] = th_status
            row['STH'] = sth_status

            # --- 待機時間 ---
            # Cloudflare対策時は少し長めにするか、エラー時に待機時間を増やすなど検討
            wait_time = 3 # 若干伸ばす
            logger.info(f"Waiting {wait_time} seconds before next request...")
            time.sleep(wait_time)

        # --- CSV書き込み ---
        # name, TH, STH 列がなければ追加
        for new_field in ['name', 'TH', 'STH']:
            if new_field not in fieldnames:
                fieldnames.append(new_field)

        logger.info(f"Writing updated data to {csv_path}")
        with open(csv_path, mode='w', newline='', encoding='utf-8-sig') as outfile:
            if not fieldnames:
                 logger.error(f"Fieldnames are empty for {csv_path}. Cannot write file.")
                 return
            writer = csv.DictWriter(outfile, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            valid_rows = [r for r in rows if isinstance(r, dict)]
            writer.writerows(valid_rows)

        logger.info(f"Successfully updated file: {csv_path}")

    except FileNotFoundError:
        logger.error(f"File not found: {csv_path}")
    except Exception as e:
        logger.error(f"An unexpected error occurred while processing {csv_path}: {e}", exc_info=True)


if __name__ == "__main__":
    logger.info("Script started")
    start_time = datetime.now()

    # --- 初期アクセスが成功した場合のみ処理を続行 ---
    if initial_access_successful:
        logger.info("Initial access was successful, proceeding with CSV processing.")
        process_all_csv_in_folder(csv_folder, scraper)
    else:
        logger.error("Initial access failed. Skipping CSV processing.")

    end_time = datetime.now()
    duration = end_time - start_time
    logger.info(f"Script completed in {duration}")