import os
import sys
import io
import contextlib
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

# グローバル変数でPaddleOCRとGeminiモデルを保持（再利用のため）
_ocr_instance = None
_gemini_model = None

def get_ocr_instance():
    """PaddleOCRインスタンスを取得（遅延初期化）"""
    global _ocr_instance
    if _ocr_instance is None:
        # paddleOCR関連のロガーのレベルを設定してメッセージを抑制
        logging.getLogger('ppocr').setLevel(logging.ERROR)
        logging.getLogger('paddlex').setLevel(logging.ERROR)
        # カラー出力を抑制するため、環境変数も設定
        os.environ['PADDLEX_LOG_LEVEL'] = 'ERROR'
        os.environ['COLORLOG_LEVEL'] = 'ERROR'
        
        # OCRモデルの読み込み（標準出力を抑制）
        try:
            # 標準出力を一時的に抑制
            with contextlib.redirect_stdout(io.StringIO()):
                _ocr_instance = PaddleOCR(lang='en')
        except Exception as e:
            logging.error(f"PaddleOCR初期化エラー: {e}")
            return None
    return _ocr_instance

def get_gemini_model():
    """Geminiモデルを取得（遅延初期化）"""
    global _gemini_model
    if _gemini_model is None:
        api_key = os.environ.get('GEMINI_API_KEY')
        if not api_key:
            return None
        genai.configure(api_key=api_key)
        _gemini_model = genai.GenerativeModel('gemini-2.5-flash')
    return _gemini_model

def process_image_paddle(image_path):
    # 再利用可能なOCRインスタンスを取得
    ocr = get_ocr_instance()
    if ocr is None:
        return ""

    # 画像を開く
    image = Image.open(image_path)
    width, height = image.size

    # 画像の上1/8を切り抜く
    top_third = image.crop((0, 0, width, height//IMAGE_CROP_SIZE))

    # Image オブジェクトから np.ndarray に変換
    img_arr = np.array(top_third)

    # 変換した画像に対してOCR処理（新しいAPIではpredictを使用）
    try:
        # 新しいAPIではpredictメソッドを使用
        result = ocr.predict(img_arr)
    except AttributeError:
        # 旧APIとの互換性のため
        result = ocr.ocr(img_arr)

    # 結果の処理
    # 新しいAPIの戻り値形式（辞書のリスト）
    if isinstance(result, list) and len(result) > 0:
        if isinstance(result[0], dict):
            # 新しい形式: result[0]['rec_texts']にテキストが入っている
            rec_texts = result[0].get('rec_texts', [])
            for text in rec_texts:
                if text and re.match(r'^[A-Z0-9]{5}', text):
                    return text[:5]
        else:
            # 旧形式: resultはネストされたリスト
            for image_result in result:
                for line in image_result:
                    text_conf = line[1]
                    text = text_conf[0]
                    
                    # 5桁の英字のみ抽出
                    if re.match(r'^[A-Z0-9]{5}', text):
                        return text[:5]

    return ""

def process_image_gemini(image_path):
    # 再利用可能なGeminiモデルを取得
    model = get_gemini_model()
    if model is None:
        logging.warning("GEMINI_API_KEY が設定されていません。Gemini APIを使用できません。")
        return ""
    
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
        
        # レスポンスの有効性をチェック
        if not response.candidates:
            logging.warning("Gemini API: レスポンスにcandidatesがありません")
            return ""
        
        candidate = response.candidates[0]
        
        # finish_reasonをチェック（文字列または数値の可能性がある）
        # 警告ログは表示しない（正常に処理できる場合は無視）
        
        # partsの存在をチェック
        if not candidate.content or not candidate.content.parts:
            logging.warning("Gemini API: レスポンスにpartsがありません")
            return ""
        
        # テキストが存在するかチェック
        first_part = candidate.content.parts[0]
        if not hasattr(first_part, 'text') or not first_part.text:
            logging.warning("Gemini API: レスポンスにテキストが含まれていません")
            return ""
        
        # テキストを取得
        result_text = first_part.text.strip()
        
        if not result_text:
            return ""
        
        # 5桁の英数字を抽出
        match = re.search(r'[A-Z0-9]{5}', result_text)
        if match:
            return match.group()
        
        return ""
    except Exception as e:
        error_msg = str(e)
        # クォータエラー（429）の場合は警告のみ（エラーにしない）
        if "429" in error_msg or "quota" in error_msg.lower() or "Quota exceeded" in error_msg:
            # クォータ超過時は警告なしでPaddleOCRの結果のみを使用
            return ""
        # その他のエラーの場合も警告なし（大量の画像処理時にログが多すぎる）
        return ""

def process_image_with_both_ocr(image_path):
    # PaddleOCRを使用してOCR処理を行う
    paddle_result = process_image_paddle(image_path)

    # Gemini APIを使用してOCR処理を行う
    gemini_result = process_image_gemini(image_path)

    if paddle_result == gemini_result:
        print(f"結果が一致しました。 (PaddleOCR: {paddle_result}, Gemini: {gemini_result})")
        return paddle_result
    else:
        # 結果の詳細を表示
        paddle_display = paddle_result if paddle_result else "(なし)"
        gemini_display = gemini_result if gemini_result else "(なし)"
        print(f"結果が異なりました。 PaddleOCR: {paddle_display}, Gemini: {gemini_display}")
        
        if paddle_result and not gemini_result:
            return paddle_result
        elif gemini_result and not paddle_result:
            return gemini_result
        else:
            # 両方とも結果がある場合はPaddleOCRを優先
            return paddle_result

def write_results_to_csv(results, csv_file):
    with open(csv_file, 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["assortment_number", "model_number", "quantity"])
        for result in results:
            writer.writerow(result)


if __name__ == "__main__":
    # PaddleOCR/PaddleXのメッセージを抑制
    logging.getLogger('ppocr').setLevel(logging.ERROR)
    logging.getLogger('paddlex').setLevel(logging.ERROR)
    os.environ['PADDLEX_LOG_LEVEL'] = 'ERROR'
    # 標準出力へのPaddleXメッセージも抑制
    import warnings
    warnings.filterwarnings('ignore')
    
    # 引数からフォルダのパスを取得
    if len(sys.argv) < 2:
        print("Usage: python script.py <image_directory>")
        sys.exit(1)

    image_dir = sys.argv[1]
    
    # PaddleOCRとGeminiを事前に初期化（最初の1回だけ）
    print("PaddleOCRを初期化中...")
    ocr = get_ocr_instance()
    if ocr:
        print("PaddleOCRの初期化が完了しました。")
    else:
        print("警告: PaddleOCRの初期化に失敗しました。")
    
    if os.environ.get('GEMINI_API_KEY'):
        print("Gemini APIを初期化中...")
        model = get_gemini_model()
        if model:
            print("Gemini APIの初期化が完了しました。")
    else:
        print("GEMINI_API_KEYが設定されていないため、Gemini APIは使用しません。")
    
    print(f"\n{image_dir} 内の画像を処理中...\n")

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