import os
import sys
import io
import google.generativeai as genai
from paddleocr import PaddleOCR
import logging
from PIL import Image
import numpy as np
import re
import csv

# .envファイルから環境変数を読み込む
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenvがインストールされていない場合はスキップ
    pass

IMAGE_CROP_SIZE = 3

def process_image_paddle(image_path):
    # paddleOCRのロガーのレベルをWARNINGに設定
    logging.getLogger('ppocr').setLevel(logging.WARNING)

    # OCRモデルの読み込み
    try:
        ocr = PaddleOCR(lang='en')
    except Exception as e:
        logging.error(f"PaddleOCR初期化エラー: {e}")
        return ""

    # 画像を開く
    image = Image.open(image_path)
    width, height = image.size

    # 画像の上1/8を切り抜く
    top_third = image.crop((0, 0, width, height//IMAGE_CROP_SIZE))

    # Image オブジェクトから np.ndarray に変換
    img_arr = np.array(top_third)

    # 変換した画像に対してOCR処理
    result = ocr.ocr(img_arr, cls=True)

    # 結果の処理
    for image_result in result:
        for line in image_result:
            text_conf = line[1]
            text = text_conf[0]
            
            # 5桁の英字のみ抽出
            if re.match(r'^[A-Z0-9]{5}', text):
                return text[:5]

    return ""

def process_image_gemini(image_path):
    # Gemini APIクライアントの設定
    # 環境変数 GEMINI_API_KEY から取得
    api_key = os.environ.get('GEMINI_API_KEY')
    
    if not api_key:
        logging.warning("GEMINI_API_KEY が設定されていません。Gemini APIを使用できません。")
        return ""
    
    genai.configure(api_key=api_key)
    
    # モデルの選択（gemini-2.5-flash は高速でコスト効率が良い）
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    # 画像の上1/3を切り抜く
    image = Image.open(image_path)
    width, height = image.size
    top_third = image.crop((0, 0, width, height//IMAGE_CROP_SIZE))
    
    # 画像を保存（一時的にメモリに保存する場合はBytesIOを使用）
    img_bytes = io.BytesIO()
    top_third.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    
    # プロンプトを作成
    prompt = """画像の上部に表示されている5桁の英数字（大文字と数字のみ）の製品コードを抽出してください。
例: ABC12, 12345, X1Y2Z など。
5桁の英数字のみを返答してください。見つからない場合は空文字を返してください。"""
    
    # 画像とプロンプトを送信
    try:
        response = model.generate_content([
            prompt,
            {"mime_type": "image/png", "data": img_bytes.getvalue()}
        ])
        
        result_text = response.text.strip()
        
        # 5桁の英数字を抽出
        match = re.search(r'[A-Z0-9]{5}', result_text)
        if match:
            return match.group()
        
        return ""
    except Exception as e:
        logging.error(f"Gemini API エラー: {e}")
        return ""

def process_image_with_both_ocr(image_path):
    # PaddleOCRを使用してOCR処理を行う
    paddle_result = process_image_paddle(image_path)

    # Gemini APIを使用してOCR処理を行う
    gemini_result = process_image_gemini(image_path)

    if paddle_result == gemini_result:
        print("結果が一致しました。")
        return paddle_result
    else:
        print("結果が異なりました。")
        if paddle_result and not gemini_result:
            return paddle_result
        elif gemini_result and not paddle_result:
            return gemini_result
        else:
            return paddle_result

def write_results_to_csv(results, csv_file):
    with open(csv_file, 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["assortment_number", "model_number", "quantity"])
        for result in results:
            writer.writerow(result)


if __name__ == "__main__":
    # 引数からフォルダのパスを取得
    if len(sys.argv) < 2:
        print("Usage: python script.py <image_directory>")
        sys.exit(1)

    image_dir = sys.argv[1]

    # ディレクトリ内の画像ファイルを名前でソート
    image_files = sorted(os.listdir(image_dir))

    results = []

    # ソート済みの画像ファイルをすべて処理
    for filename in image_files:
        image_path = os.path.join(image_dir, filename)
        if os.path.isfile(image_path):
            result = process_image_with_both_ocr(image_path)
            if result:
                print(f"File: {filename}, Result: {result}")
                assortment_number = os.path.basename(image_dir)
                model_number = result

                # 結果の配列に追加
                found = False
                for i, r in enumerate(results):
                    if r[0] == assortment_number and r[1] == model_number:
                        results[i][2] += 1
                        found = True
                        break
                if not found:
                    results.append([assortment_number, model_number, 1])

    # 結果をCSVファイルに書き込む
    # ディレクトリ名をベースにCSVファイル名を生成
    dir_name = os.path.basename(image_dir)
    csv_file = f"{dir_name}.csv"
    write_results_to_csv(results, csv_file)
    print(f"Results saved to {csv_file}")