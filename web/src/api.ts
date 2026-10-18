/** Some server conditions and results are reported in Traditional Chinese.
 * The web UI is English-only, so known messages are shown in English. */
const englishMessages: Record<string, string> = {
  "尚未設定文字模型；目前可以使用本機規則示例。": "No text model is configured yet; the local rule-based demo is available.",
  "尚未設定文字模型。": "No text model is configured yet.",
  "本次內容超過 MVP 的 100 條記憶限制，請縮小日期範圍。": "This exceeds the MVP limit of 100 memories; please narrow the date range.",
  "模型輸出不是有效 JSON；保留原本卡片。": "The model output was not valid JSON; the original cards were kept.",
  "模型輸出須為卡片物件；保留原本卡片。": "The model output must be a card object; the original cards were kept.",
  "模型輸出格式不符；保留原本卡片，請重試。": "The model output format is invalid; the original cards were kept. Please try again.",
  "模型卡片格式不符；保留原本卡片。": "A model card has an invalid format; the original cards were kept.",
  "模型卡片類別不符。": "The model card category is invalid.",
  "模型引用了無效來源；保留原本卡片。": "The model cited an invalid source; the original cards were kept.",
  "模型沒有提供有效摘要。": "The model did not provide a valid summary.",
  "模型摘要過長；保留原本卡片。": "The model summary is too long; the original cards were kept.",
  "文字模型的關懷輸出不是有效 JSON。": "The text model's care response was not valid JSON.",
  "文字模型的關懷輸出須為物件。": "The text model's care response must be an object.",
  "文字模型的關懷文案格式不符。": "The text model's care text has an invalid format.",
  "日期格式須為 YYYY-MM-DD。": "Dates must use the YYYY-MM-DD format.",
  "錄音時間格式不正確。": "The recording time format is invalid.",
  "本 MVP 提供英文或自動語言辨識。": "This MVP supports English or automatic language detection.",
  "檔案超過上傳大小限制。": "The file exceeds the upload size limit.",
  "錄音檔案是空的。": "The recording file is empty.",
  "錄音超過 MVP 的時長限制，請先分段。": "The recording exceeds the MVP length limit; please split it first.",
  "這個錄音目前不需要重試。": "This recording does not need a retry.",
  "原始錄音已不存在，請重新匯入。": "The original recording no longer exists; please import it again.",
  "記憶內容不能是空白。": "Memory content cannot be blank.",
  "找不到這個 API。": "This API was not found.",
  "找不到這項資料，或資料已刪除。": "This item was not found or has been deleted.",
  "這項資料已刪除。": "This item has been deleted.",
  "找不到這個資料集，或資料集已被刪除。": "This dataset was not found or has been deleted.",
  "資料集檔案是空的。": "The dataset file is empty.",
  "資料集超過 5 MB，請先分拆檔案。": "The dataset exceeds 5 MB; please split the file first.",
  "資料集超過 1000 項，請先分拆檔案。": "The dataset exceeds 1000 items; please split the file first.",
  "資料集沒有可匯入的項目。": "The dataset has no importable items.",
  "資料集須使用 UTF-8 編碼。": "Datasets must use UTF-8 encoding.",
  "資料集檔名格式不正確。": "The dataset filename is invalid.",
  "資料集內容須為檔案位元組。": "Dataset content must be file bytes.",
  "JSON 物件包含重複欄位，請移除有歧義的資料。": "A JSON object has duplicate fields; please remove the ambiguous data.",
  "JSON 不允許 NaN 或 Infinity，請提供有效數值。": "JSON cannot contain NaN or Infinity; please provide valid numbers.",
  "文字模型未回應或格式不符；原卡片已保留。請檢查本機模型／設定。": "The text model did not respond or returned an invalid format; the original cards were kept. Please check the configured model and settings.",
  "跟進事項格式不正確。": "The follow-up format is invalid.",
  "跟進事項的許可及完成狀態須為布林值。": "Follow-up permission and completion must be true or false.",
  "請先確認原話屬於本人，再核實或修正理解。": "Confirm that the original words are yours before verifying or correcting the understanding.",
  "記憶內容須為 1 至 10,000 字。": "Memory content must be between 1 and 10,000 characters.",
  "記憶已被更新，請重新載入最新版本再修改。": "This memory has been updated; reload the latest version before editing.",
  "記憶已更新，舊提示已取消。": "The memory was updated and the old suggestions were cancelled.",
  "來源記憶已刪除，提示已取消。": "The source memory was deleted and the suggestion was cancelled.",
  "請至少選擇一段屬於自己的片段。": "Select at least one segment that is yours.",
  "選取的片段不屬於這段錄音。": "The selected segments do not belong to this recording.",
  "卡片摘要或來源已更新，請重新查看再核實。": "The card summary or sources have changed; review them again before confirming.",
  "仍有來源未確認屬於本人，無法核實卡片。": "Some sources are not yet confirmed as yours, so the card cannot be verified.",
  "請選擇未來的香港時間設定睡覺提醒。": "Choose a future Hong Kong time for the bedtime reminder.",
  "這項休息建議或來源已更新，請重新評估。": "This rest suggestion or its sources changed; please evaluate again.",
  "只有有效的睡眠模式支持卡可以設定睡覺提醒。": "Only a valid sleep-pattern support card can set a bedtime reminder.",
  "缺少今天已核實的來源，不能設定提醒。": "No verified source from today is available, so a reminder cannot be set.",
  "不支援的回饋動作。": "This feedback action is not supported.",
  "這項提示已失效或已處理，請重新載入。": "This suggestion is no longer active; please reload.",
  "只有跟進事項可以標示完成。": "Only follow-up items can be marked as done.",
  "主動關懷文案長度或格式不正確。": "The care wording length or format is invalid.",
  "主動關懷來源版本格式不正確。": "The care source version format is invalid.",
  "關懷條件或來源版本已改變，請重新評估。": "The care conditions or source versions changed; please evaluate again.",
  "展示前條件已改變，關懷文案已取消。": "Conditions changed before display, so the care text was cancelled.",
  "不支援的錄音處理狀態。": "This recording status is not supported.",
  "逐字稿片段格式不正確。": "The transcript segment format is invalid.",
  "逐字稿時間戳不正確。": "The transcript timestamps are invalid.",
  "模型摘要格式不正確。": "The model summary format is invalid.",
  "模型處理期間來源已更新，請重新生成摘要。": "Sources were updated while the model was working; please regenerate the summary.",
  "模型摘要依據尚未確認主體。": "The model summary is based on speakers who are not yet confirmed.",
  "模型摘要的類型、內容或來源格式不正確。": "The model summary type, content or sources are invalid.",
  "模型摘要的來源範圍已改變，請重新生成。": "The model summary source scope changed; please regenerate it.",
  "時間格式不正確，請使用 ISO8601。": "The time format is invalid; please use ISO8601.",
  "錄音缺少檔案雜湊。": "The recording is missing its file hash.",
  "偏好中包含不支援的欄位。": "Preferences contain an unsupported field.",
  "關懷類型偏好須為布林值。": "Care type preferences must be true or false.",
  "開關須為布林值。": "Switches must be true or false.",
  "頻率須為 0–1440 分鐘；保留期限須為 1–3650 日。": "Frequency must be 0–1440 minutes and retention 1–3650 days.",
  "找不到這張每日卡片。": "This daily card was not found.",
  "找不到這個資料集。": "This dataset was not found.",
  "無法讀取錄音的取樣率。": "The recording's sample rate could not be read.",
  "檔案沒有可讀取的音訊軌，請選擇錄音檔案。": "This file has no readable audio track; please choose a recording.",
  "錄音沒有可解碼的音訊，請檢查檔案是否損壞。": "The recording has no decodable audio; please check whether the file is damaged.",
  "找不到錄音檔案，請重新匯入。": "The recording file was not found; please import it again.",
  "錄音檔案是空的，請選擇有效的音訊。": "The recording file is empty; please choose valid audio.",
  "未安裝音訊解碼套件 PyAV，請完成後端依賴安裝。": "The PyAV audio decoder is not installed; please finish installing the backend dependencies.",
  "無法解碼錄音，請檢查檔案是否損壞或使用 WAV、MP3、M4A、FLAC。": "The recording could not be decoded; check whether the file is damaged or use WAV, MP3, M4A or FLAC.",
  "本 MVP 支援英語或自動語言偵測，請選擇 en 或 auto。": "This MVP supports English or automatic detection; please choose en or auto.",
  "轉錄時間戳無效，請重試。": "The transcription timestamps are invalid; please try again.",
  "轉錄服務未就緒。": "The transcription service is not ready.",
  "本地模型目錄缺少 model.bin，請提供 CTranslate2 格式的 Whisper 模型。": "The local transcription model files are missing; check the server's ASR configuration.",
  "轉錄失敗，請檢查本地模型／網絡下載、磁碟空間及 CUDA 環境後重試；系統沒有改用其他模型。": "Transcription failed. Check the local model/network download, disk space and CUDA environment, then retry; the system did not switch to another model.",
  "CUDA 未就緒：請檢查 NVIDIA 驅動、CUDA 12 cuBLAS 與 cuDNN 9。": "CUDA is not ready: check the NVIDIA driver, CUDA 12 cuBLAS and cuDNN 9.",
  "未安裝 faster-whisper，請完成後端依賴安裝。": "The configured transcription service dependencies are not installed; finish the backend setup.",
  "無法初始化 CTranslate2，請檢查轉錄套件及執行環境。": "CTranslate2 could not be initialised; check the transcription packages and runtime.",
  "無法讀取這個音訊檔案，請檢查格式或重新匯出。": "This audio file could not be read; check the format or export it again.",
  "請選擇 WAV、MP3、M4A、FLAC、OGG、WEBM 或 MP4 錄音。": "Choose a WAV, MP3, M4A, FLAC, OGG, WEBM or MP4 recording.",
  "請選擇 JSONL 格式的資料集。": "Choose a JSONL dataset.",
  "資料集超過 5 MB 上限。": "The dataset exceeds the 5 MB limit.",
  "請先提供跟進時間，或取消允許跟進。": "Provide a follow-up time, or turn off the follow-up permission.",
};

export function englishMessage(message: string): string {
  const direct = englishMessages[message];
  if (direct) return direct;
  if (/^錄音超過 .+ 分鐘，請先分段再匯入。$/.test(message))
    return "The recording exceeds the length limit; please split it first.";
  const numbered = /^第 (\d+) 行：(.+)$/.exec(message);
  if (numbered) {
    const rest =
      englishMessages[numbered[2]] || "This line is not valid dataset data.";
    return `Line ${numbered[1]}: ${rest}`;
  }
  if (/[\u4e00-\u9fff]/.test(message))
    return "The request could not be completed. Please try again.";
  return message;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { ...options, headers });
  } catch {
    throw new ApiError(
      "Could not reach the service. Check that it is running, then reload.",
      0,
    );
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new ApiError(
      typeof data.detail === "string"
        ? englishMessage(data.detail)
        : "The action did not complete. Please try again.",
      response.status,
    );
  return data as T;
}
