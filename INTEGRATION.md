# MVP 整合契約（2026-10-13）

## v0.2 主動式 AI 補充（優先於下列舊契約）

主體確認與理解核實分開：`confirmed` 只表示片段確定屬於本人；新記憶 `reviewed=false`，不能立即作主動支持依據。每日卡片可以展示待核實的本人記憶。使用者修改記憶並明確保存後 `reviewed=true`；原始逐字稿不改。

- 每日卡片新增 `review_status: pending|confirmed`、`review_prompt`、`review_token`（摘要文字及來源版本的雜湊）。生成後立即展示「這些事情有發生嗎？我有理解對你嗎？需要怎樣修改？」；每張卡可確認或進入來源修改。
- `Engine.confirm_card(card_id, expected_versions, review_token)`：核對完整來源集合、版本、摘要 token，再把該卡片記憶設為已核實；過期回 409。新摘要、增刪或修正使原卡片核實狀態失效。主體未知的記憶不能核實。
- `POST /api/cards/{card_id}/confirm` JSON `{expected_versions:{id:version}, review_token:string}` → state。`revise_memory` 明確確認的修正標記 reviewed=true；被修正卡片仍主動請核實新摘要。
- 主動支持僅使用有效、屬本人、已核實的記憶。歷史候選同時保留過往與當前的全部來源及版本；修改、刪除、偏好或條件改變即失效。
- `sleep_pattern`：MVP 示範規則為最近七天三個不同日期（含今天）的記錄明確表述睡眠不足；跨日歷史＋今天才提出休息建議，並詢問是否設定睡覺提醒。這是可見的產品示範規則，並非心理或疾病判斷門檻。同檔重複上傳不增加日數。
- `social_follow_up`：已核實歷史明確表述孤單／社交困難，今天明確表述與朋友交談且感覺好些，才主動給鼓勵；`emotional_follow_up`：過往與今天明確表述壓力，且今天要求支持，才連結歷史提供安慰。必須列出兩個時段的出處，不推斷人格或診斷。
- 主動卡新增 `support_basis`、`historical_memory_ids`、`current_memory_ids`、`can_schedule_bedtime`。四類門檻、免打擾、頻率、單類選擇、版本重查沿用。警示仍為清楚標示的虛構流程演示，不聲稱臨床預警。
- `Engine.schedule_bedtime(card_id, due_at, expected_versions)`、`POST /api/proactive/{id}/bedtime` JSON `{due_at,expected_versions}`：用戶主動選擇未來香港時間後，才建立已允許的跟進事項；只能用有效 sleep_pattern 卡。提醒在應用開啟時檢查；沒有 OS 背景鬧鐘。
- `POST /api/demo/scenario` 支援新增 `sleep|social_history|emotional_history`；虛構歷史清楚標示，情境重置不動真實資料。
- 本機每日摘要要使用繁體中文歸納明確錄音內容，原話保留在來源；標示本機規則而非偽稱模型生成。沒有可識別內容時直接說明證據不足，不編造情緒或心理分數。

## 專業資料集入口（後期後訓練）

`DatasetStore(data_dir)` 獨立模組 `dataset.py`：本機 JSONL 匯入、列表、明細、刪除。每項必填 `kind`（knowledge|counselling_case|guidance）、`title`、`content`、`source`、`license`、`deidentified:true`、`consent_to_use:true`，可選 `professional_reviewer`；所有匯入均 `status=pending_review`、`training_enabled=false`、`runtime_enabled=false`。缺欄位／未去識別／未授權拒收；上限 5 MB／1000 筆。提供明確虛構格式範例，不能把範例說成專業資料。

HTTP：`GET /api/datasets` → `{datasets:[], purpose, training_enabled:false}`；`POST /api/datasets` multipart file → 匯入摘要；`GET /api/datasets/{id}` → 本機明細；`DELETE /api/datasets/{id}`。UI 偏好內加入「專業資料集入口」上傳和狀態，不會自動送去模型或啟動訓練；原檔及記錄在 ignored data/ 下。未經專業審核的資料不用作心理健康預警。

僅軟件 MVP。香港繁體中文介面；英語原話保留。前端重用 MoodHaven 的 MIT UI 設計／元件樣式，React/Vite；一個 FastAPI 服務提供建置網頁與 API；SQLite＋本地音訊；faster-whisper large-v3-turbo。不要上傳私人錄音、金鑰、模型、資料庫或其他工作區資料。

## 檔案分工

- web/：前端代理。
- backend/hya/engine.py 與 tests/test_engine.py：記憶／關懷代理。
- backend/hya/asr.py 與 scripts/verify_asr.py、ASR 測試／整合記錄：ASR 代理。
- backend/hya/app.py、schemas/config、啟動與打包、文件、整合測試、Git：root。

## 共用資料

ID 使用字串 UUID。時間為 ISO8601 帶香港時區；event_time 可為 null，不能拿匯入時間冒充。date 為 YYYY-MM-DD。memory：id, recording_id, segment_id, content, subject_id（user / unknown）, memory_type（episodic / semantic）, evidence_kind（direct_expression / user_confirmed / ai_inference）, scope, event_time, source_refs（segment_id/start/end/text）, category（mood / social / moments）, status（pending / active / superseded / deleted）, version, confirmed, follow_up（object 或 null）。所有範例標示 is_demo=true。原始文字獨立保存。

## Engine 介面

Engine(data_dir: str|Path)：建立 SQLite/目錄，並以每個方法的新 connection 支援多執行緒。

- state(date: str|None=None) -> dict：{date, dates: [str], recordings: [], memories: [], cards: [], proactive: [], settings: {}, coverage: {}, assessment: {reason, matched_types:[]}}。
- create_recording(filename, file_path, sha256, recorded_at, is_demo=False) -> dict：重複雜湊只回既有 recording（duplicate=true），不要增加證據。
- get_recording(id) / list_recordings() -> dict/list。
- set_recording_status(id,status,error=None,metrics=None) -> dict。
- ingest_segments(recording_id,segments,language='en',subject_confirmed=False) -> dict：segments=[{start,end,text,speaker:'unknown',words?:[]}];真實 ASR 沒有已確認 speaker，全部 pending，須人工確認主體。
- confirm_recording(recording_id, segment_ids=None) -> dict：只把指定片段（None=用戶明確整段屬自己）設為 user 並生成／更新記憶。
- get_source(memory_id) -> dict：{memory, recording, segments, versions}，已刪除內容不得返回。
- revise_memory(id,content,version,confirmed=True,follow_up=None) -> dict：樂觀鎖，不符版本拒絕；舊版本 superseded；同步重算相關卡片並取消候選。
- delete_memory(id) -> dict：清除該記憶內容（包括歷史舊內容及衍生引用），保留不含內容 tombstone；同步重算／取消。
- delete_recording(id) -> dict：刪來源檔、片段、關聯記憶／歷史和衍生輸出。
- settings_patch(dict) -> dict：preferences 各類布林提醒 reminder/suggestion/comfort/warning；quiet_mode；frequency_minutes；warning_demo_enabled=false；保留期限 retention_days。
- evaluate(date=None,now=None) -> dict：{cards: [], assessment:{reason,matched_types:[]}}；預設多類匹配不展示；不能自行訂疾病／風險門檻。警示只允許明確虛構演示情境與 warning_demo_enabled。
- feedback(card_id,action) -> dict：action=snooze/dismiss/done/helpful；snooze/dismiss 改接收狀態，不寫個人事實；done 更新事項狀態；展示前重查來源版本／接收偏好。
- seed_demo() -> dict：固定可重置的虛構英語樣例，去重，展示三類每日卡片、可修正來源與四類各自可選演示。不存在的錄音不偽裝真實音訊。

卡片：{id,category,title,summary,date,memory_ids,source_refs,versions,recording_count,status,is_demo,provisional}。主動卡片另有 type,condition_reason,action_text。Engine 的小型規則式文字抽取要標示 extraction_mode='local_rules'；可選 LLM 由 root 接 adapter，但不默默向外傳音訊或文字。faster-whisper 是真實 ASR。

## HTTP 契約

- GET /api/health；GET /api/state?date=；GET /api/recordings。
- POST /api/recordings（multipart file、recorded_at、language=en）→ {recording,duplicate}; 背景真實 ASR，最大100MB／20分鐘，可配置。UI 輪詢 state。
- POST /api/recordings/{id}/retry；POST /api/recordings/{id}/confirm JSON {segment_ids?:[]}；DELETE /api/recordings/{id}。
- GET /api/memories/{id}/source；PATCH /api/memories/{id} JSON {content,version,confirmed:true,follow_up?:{due_at,allowed,completed}}；DELETE /api/memories/{id}。
- GET /api/recordings/{id}/audio（只讀有效來源，支援 Range）。
- PATCH /api/settings；POST /api/proactive/evaluate JSON {date?}；POST /api/proactive/{id}/feedback JSON {action}。
- POST /api/demo/seed；POST /api/demo/scenario JSON {type:'reminder'|'suggestion'|'comfort'|'warning'|'none'|'multiple'} 切換明確標示演示資料的單類、零類、多類例。

API errors：{detail:'繁體中文說明'}，HTTP 409 for stale revision，404 for unknown/deleted。前端 api 相對路徑，開發 proxy localhost:8765。首頁今日卡片，側欄：今日卡片、錄音匯入、記憶與來源、主動關懷。詳情側欄可回放來源與修改；返回保留原日期及選中卡片。關懷畫面顯示為何未觸發。讓 empty/loading/failure/success 均有清楚狀態。可在偏好面板開關類型、免打擾、模型狀態。模式及示例/真實能力區分清楚。
