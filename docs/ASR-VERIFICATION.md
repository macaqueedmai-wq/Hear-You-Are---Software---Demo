# 本機錄音轉錄驗證

日期：2026-10-13（香港）｜版本：MVP v0.1｜撰寫：Codex

## 一、已實作

使用 **Whisper large-v3-turbo＋faster-whisper**，錄音在本機解碼及推論。首次使用才載入模型；健康檢查不下載模型。所有 GPU 工作以程序內共用鎖順序處理，並重用同一模型。

- 自動偵測 CUDA；缺少驅動或必要函式庫時，使用相同模型的 CPU。明確指定 CUDA 但環境未就緒時回報錯誤。
- CUDA 預設 `float16`，CPU 預設 `int8`；維持 `beam_size=5`、VAD、詞級時間戳。
- PyAV 在模型載入前實際解碼音訊、確認音軌及時長，預設上限 20 分鐘。原件不被覆寫。
- 英語原話保留，時間戳對應原始錄音；自動轉錄沒有說話者身份能力，片段一律 `speaker='unknown'`，待用戶確認。
- 模型下載、CUDA 或解碼失敗會回報明確狀態，沒有偷偷換用小模型或假逐字稿。

模型名稱由 [faster-whisper 1.2.1](https://github.com/SYSTRAN/faster-whisper/tree/v1.2.1) 對應到 `mobiuslabsgmbh/faster-whisper-large-v3-turbo`；該 Hugging Face 名稱現轉向 [dropbox-dash 的 CTranslate2 轉換](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo)。來源權重是 OpenAI 的 `whisper-large-v3-turbo`，維持相同模型。可用 `HYA_ASR_MODEL_DIR` 指定已下載的 CTranslate2 目錄；需同時有 `model.bin`、設定及 tokenizer，不是原始 PyTorch 目錄。

## 二、實際 GPU 測試

測試機：Windows、RTX 4060 Laptop 8 GB（驅動 592.82）、Python 3.12 專案 `.venv`。

使用 Windows **Microsoft Zira Desktop** 產生虛構英文錄音，42 詞、19.001 秒，前置 3 秒靜音。沒有使用私人錄音，也沒有上傳音訊。執行：

```powershell
.\.venv\Scripts\python.exe scripts/verify_asr.py --generate --runs 2 --model-dir models/large-v3-turbo
```

- 第一次：模型載入 **2.068 秒**，推論 **0.961 秒**，合計 **3.030 秒**（不含模型下載）。
- 模型已載入的第二次：推論 **0.475 秒**，約 **39.97 倍**即時速度。
- 逐字比較 WER 為 **2.38%**，唯一差異是原稿 `nine` 被寫成 `9`；其他 41 詞相同。這是乾淨合成語音的功能測試，不能推論成港式口音、多人對話或嘈雜錄音的準確度。
- 第一個片段從原音訊 **2.770 秒**開始，VAD 保留來源時間位置；所有片段仍是身份未確認。

另外以同一錄音實測 CPU `int8`：載入 **3.263 秒**、推論 **5.666 秒**、合計 **8.929 秒**，轉錄與 GPU 結果相同。這確認 CPU 路徑可以運行相同模型；不同電腦的速度會不同。

原稿：

> Today I felt nervous before my presentation. I talked with my friend Sarah after class, and our conversation helped me feel better. Tomorrow I need to review my slides at nine in the morning. Please remind me to prepare before the meeting.

完整本機報告與測試 WAV 位於 `data/verification/`，已排除 Git；上傳到 GitHub 的是驗證腳本及本說明，不包括錄音、權重或資料庫。

測試權重 commit：`0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`。

`model.bin` 大小：`1617884929` bytes；SHA-256：`e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da`。

本機只有未完成的舊快取；驗證時將該副本複製至專案忽略的 `models/large-v3-turbo`，從官方 Hub 補齊尾段，再核對完整 SHA-256。沒有覆寫既有快取或使用其他工作區的私人音訊。

## 三、環境與安裝

已驗證套件版本：`faster-whisper 1.2.1`、`ctranslate2 4.8.2`、`av 18.1.0`、`onnxruntime 1.31.0`、`tokenizers 0.23.3`、`huggingface-hub 2.2.0`。

Windows GPU 測試套件：`nvidia-cublas-cu12 12.9.2.10`、`nvidia-cudnn-cu12 9.27.0.42`、`nvidia-cuda-nvrtc-cu12 12.9.86`。適配器自動登記目前 Python 環境的 NVIDIA `bin` 目錄；另外安裝的函式庫可透過 `HYA_CUDA_DLL_DIRS` 提供多個以系統路徑分隔符分開的目錄。CUDA 套件及模型下載較大，應在演示前完成安裝及模型預載。

首次下載使用 Hugging Face；本機推論不需要將音訊傳到 Hub。需要完全離線時，先準備模型並設 `HYA_ASR_OFFLINE=true`。自訂快取可設 `HYA_MODEL_CACHE`；亦尊重 Hugging Face 的 `HF_HOME`。`HF_HUB_DISABLE_XET` 預設為 `1`，可按環境明確覆蓋。

## 四、可重跑檢查

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'backend'
.\.venv\Scripts\python.exe -m pytest tests/test_asr.py -q
```

五項檢查已通過：模型未載入時解碼、損壞／缺失檔案、時長上限、來源時間與未確認身份、模型載入失敗狀態。測試不下載權重；上面的實際轉錄另外證明模型推論成功。

後續準確度驗收應加入獲授權的自然英文、港式口音、中英混合、多人及噪音樣本；目前演示預設英文。
