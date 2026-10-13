# 開源元件與授權

- **MoodHaven Journal**：Copyright (c) 2025 Ken LaCroix and MoodHaven Journal contributors，MIT。前端 SidebarItem、Violet 色票、卡片及導覽樣式由上游裁剪改寫。完整授權與來源快照見 [third_party/moodhaven](third_party/moodhaven)，具體映射見 [UI-SOURCE.md](docs/UI-SOURCE.md)。上游：https://github.com/kenlacroix/moodhaven-journal 。
- **faster-whisper**：MIT，https://github.com/SYSTRAN/faster-whisper 。以 CTranslate2 執行本機模型，程式透過 Python 套件依賴使用；沒有複製其模型檔案到倉庫。
- **Whisper large-v3-turbo**：OpenAI Whisper 開放模型，MIT；權重由 faster-whisper 對應的 CTranslate2 轉換倉庫下載。實際來源、模型雜湊與驗證見 [ASR-VERIFICATION.md](docs/ASR-VERIFICATION.md)。上游：https://github.com/openai/whisper 。
- **React／Vite／TypeScript／Lucide**：透過 npm 套件使用；授權以各套件內附 LICENSE 為準。鎖定版本見 web/package-lock.json。
- **FastAPI／Uvicorn／PyAV／CTranslate2／ONNX Runtime**：透過 Python 套件使用；授權以各發行套件為準。NVIDIA CUDA 函式庫為可選執行依賴，使用其供應商授權，不以本項目的 MIT 聲明重新授權。

本倉庫不包含 MoodHaven 的第三方照片、音效、私人資料或上游 Git 歷史。
