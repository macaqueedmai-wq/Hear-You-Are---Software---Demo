# 目前整合契約（2026-10-18：自動流程與可編輯主題）

本節優先於後方歷史快照。新版本程式與本機驗證已更新；是否完成最新雲端發布，須以當次部署記錄確認。

- **Uploading date** 唯一來源是伺服器建立錄音時保存的 `created_at`，以香港 UTC+8 顯示。新錄音的相容日期欄位同步使用這個時間；既有資料由遷移處理。這是上傳時間，不代表實際事件發生時間。前端不傳 `recorded_at`、不提供日期編輯器。
- 上傳自動執行 **Transcribing → Atomic memories → Topics**，並自動更新摘要及主動支持；正常流程不要求再按 Process、核准 AI 理解或 Check conditions。失敗可重試，模型失敗不能偽稱成功。
- 說話者確認仍獨立：原始片段保持匿名／未知，只有明確提交 **Confirm N segments as mine** 才歸屬為本人。編輯主題／原子記憶不會代為確認身分或授予許可。
- 主動支持不再以 `reviewed`／AI 理解核准為門檻；仍檢查有效來源、說話者歸屬、使用者偏好、跟進許可、各類條件、免打擾、頻率及版本。
- **Atomic memories** 按主題 1、2、3… 展示；每個主題均有 **Edit topic**。原子記憶可顯示 1.1、1.2…，說話者未確認亦可編輯。每日卡片提供摘要、Sources 及 Edit topic，不提供 That's right／Needs changes 核准步驟。

## 主動支持顯示時間與即時顯示

四類支持各自提供 `assessment.types[type].display_time={scheduled_at,effective_at,displayed_at,reason}`，以香港 UTC+8 顯示預定、最早自動顯示及最近已顯示時間；無法確定的時間明確說明。這是單次最早顯示時間，不是背景系統通知或每日循環排程。

`PATCH /api/proactive/display-time` JSON `{type,display_at:ISO|null,expected_display_at:原值|null}` 修改指定類型時間；null 回到自動安排。後端正規化時區並以舊值防止覆蓋其他編輯，衝突回 409，前端保留草稿。設為未來會延後已顯示支持；時間到後仍檢查來源、偏好、免打擾及 snooze。

每類的 **Edit display time** 旁為 **Display Proactive Support Now**，取代 Comfort me now。`POST /api/proactive/display-now` JSON `{type,date?}` 回傳 `{cards,assessment,care_generation}`。手動顯示只跳過送達排程，不修改已存時間或全域偏好；仍需當前、身分已確認的有效來源與各類條件／許可，Warning 保留虛構示範開關。重複請求共用當前版本卡片與模型工作。舊 comfort-now API 保留相容，前端不再提供該按鈕。

**Preferences & model → Warning** 控制真實錄音的關懷警示，僅更新 `preferences.warning`，不依賴舊的 `warning_demo_enabled`。觸發條件是今天明確表達強烈困擾，或最近七天（含今天）至少三個不同日期明確表達同一類情緒／社交困擾。只使用已確認屬於用戶、有效且有時間的原文引句或用戶修正；模型改寫不能創造觸發條件，重複記憶不會增加日期數。卡片提供 `support_basis=acute_distress|repeated_distress` 及 `warning_trigger={basis,topics,dates,day_count,window_days,evidence_quotes}`，保留來源鏈結、編輯撤回與版本重查。真實 Warning 在自動類別選擇時優先；排程、免打擾和頻率仍有效。DeepSeek 使用原文語言生成簡短支持，不作診斷或風險預測。預設仍關閉，不修改現有偏好；舊虛構範例保留雙權限與清楚標示。

## 持久化主題與編輯 API

每日摘要在片段確認歸屬後自動生成。`state.summary_generation` 為 `{status,message}`，狀態是 `no-sources|queued|running|failed|unconfigured|ready`。前端只在 queued／running 顯示生成中並加快輪詢；只有 failed 提供 Retry summary。正常流程沒有額外 Generate 點擊。`POST /api/analysis/generate` 沿用作明確重試，會加入同一版本來源的背景工作。事實修正／刪除／新增有效來源後，自動更新相關每日摘要；原始音訊、片段文字、說話者確認及許可不變。

自動摘要與記憶 consolidation 是相鄰的不同步驟：後者會在暫定記憶階段提出重複／衝突建議並整理主題，使用者確認說話者後，摘要讀取有效記憶顯示卡片。Consolidation 的 LLM 建議仍是 advisory，不會自動合併或覆寫使用者事實。

`recording.topics` 是權威主題清單，每項為 `{id,title,summary,source_segment_ids,version,edited,memory_ids,number}`。`number` 是按時間排序的 1 起算編號；來源 ID 必須完整、連續且依時間分割原始片段。主題不依賴暫時的 `memory_pipeline` 報告，確認身分、編輯或該報告變為 stale 時仍保留。使用者已修改的主題與原子事實不被重試模型覆蓋。

`PATCH /api/recordings/{recording_id}/topics/{topic_id}`：

```json
{"version":3,"title":"Topic title","summary":"Topic summary","memories":[{"id":"memory-id","version":2,"content":"Edited atomic memory"}]}
```

`memories` 可省略；前端只提交修改過的同主題原子記憶。限制為標題 120、摘要 2000、每條原子記憶 4000 字元。成功直接回傳更新後 Recording，再刷新 state 取得原子內容／版本。任何主題或原子版本衝突回 409；前端保留輸入，使用者明確刷新保存值、比較後才可再次保存。原音、逐字稿時間／文字、說話者身分及許可不變。

`recording.topic_pipeline` 單獨追蹤既有逐字稿整理的 pending／processing／completed／failed。後端可用 `POST /api/recordings/{id}/organize` 背景整理；正常上傳不需要前端額外點擊。`memory_pipeline` 的舊建議變 stale 不代表權威主題失效。

語音預設為本機／主機 SenseVoiceSmall INT8＋VAD＋匿名說話者分群；逐字稿及記憶文字仍傳往設定的官方 DeepSeek 端點。金鑰只在後端。來源、記憶、刪除、偏好、提醒及資料集等非衝突 API 介面沿用；專業資料集入口仍不自動啟用後訓練。

目前前端通過 production build 及 32 項 helper 測試，包含 Uploading date、持久化編號主題、未確認身分時的編輯、CAS 草稿保存，以及移除舊日期／理解核准入口。歷史 ASR 基準不能證明新流程已完成雲端驗收。

## 歷史快照：2026-10-13 MVP（已被上方政策取代）

下列原文保留追溯用途；其中手動錄音時間、Whisper 預設、理解核准／`reviewed` 門檻及 That's right 流程不是目前產品規則。

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
