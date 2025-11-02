import os
import pandas as pd

# CSVファイルが保存されているフォルダのパス
folder_path = 'csv'

# フォルダ内のすべてのCSVファイルを読み込み
csv_files = [file for file in os.listdir(folder_path) if file.endswith('.csv')]

# CSVファイルを結合するためのリスト
df_list = []

# 各CSVファイルを読み込み、リストに追加
for csv_file in csv_files:
    file_path = os.path.join(folder_path, csv_file)
    df = pd.read_csv(file_path)
    df_list.append(df)

# すべてのCSVを1つのDataFrameに結合
combined_df = pd.concat(df_list, ignore_index=True)

# 結合した結果を新しいCSVとして保存
combined_df.to_csv('combined_csv_output.csv', index=False)

print("CSVファイルが結合され、'combined_csv_output.csv'に保存されました。")
