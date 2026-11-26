"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import { request } from "@/lib/api";
import { usePlantcare } from "../plantcare-context";
import styles from "./chat.module.css";

type Mode = "cloud" | "local" | "auto";
type Conversation = { id: string; title: string; default_mode: Mode };
type ConversationPage = { items: Conversation[]; next_offset: number | null };
type Source = {
  kind: string;
  ref: string | null;
  timestamp: string | null;
  data_source: string | null;
  stale: boolean;
  model_version: string | null;
  citation?: {
    title: string;
    publisher: string;
    section: string;
    version: string;
    revision: string;
    url: string | null;
  };
};
type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  details: {
    agents_used?: string[];
    sources?: Source[];
    llm?: {
      actual_provider: "cloud" | "local";
      model: string;
      fallback_used: boolean;
    };
  };
};
type MessagePage = {
  items: Message[];
  conversation: Conversation;
  next_before_turn: number | null;
};
type Turn = {
  conversation: Conversation;
  user_message: Message;
  assistant_message: Message;
};
type ProviderInfo = {
  default_mode: Mode;
  auto_preference: "cloud" | "local";
  providers: { name: "cloud" | "local"; model: string; configured: boolean }[];
};

const labels: Record<string, string> = {
  cloud: "Cloud",
  local: "Local",
  auto: "Tự động",
  iot: "Thiết bị",
  irrigation: "Tưới",
  weather: "Thời tiết",
  plant_health: "Phân tích ảnh",
  supervisor: "Trợ lý",
  device: "Trạng thái thiết bị",
  sensor: "Số đo",
  command: "Phản hồi lệnh",
  image: "Kết quả ảnh",
  alert: "Cảnh báo",
  knowledge: "Tra cứu tài liệu",
  simulation: "nguồn simulation",
  demo_scenario: "kịch bản DEMO",
  openweather_current: "quan trắc OpenWeather",
};
const questions = [
  "Độ ẩm đất hiện tại bao nhiêu?",
  "Vì sao hệ thống chưa tưới?",
  "Dự báo mưa còn hiệu lực không?",
  "Ảnh gần nhất có bao nhiêu quả chín?",
  "Ảnh lá mới nhất gợi ý gì và nên kiểm tra dấu hiệu nào?",
];
const dateTime = new Intl.DateTimeFormat("vi-VN", {
  timeZone: "Asia/Ho_Chi_Minh",
  dateStyle: "short",
  timeStyle: "short",
});

function errorText(error: unknown) {
  if (error instanceof Error) {
    if (error.name === "TimeoutError")
      return "Phản hồi quá thời gian. Hãy thử gửi lại.";
    return error.message;
  }
  return "Chưa kết nối được trợ lý.";
}

function AnswerDetails({ details }: { details: Message["details"] }) {
  const { llm, sources, agents_used: agents } = details;
  const citedSources = sources?.filter((source) => source.kind === "knowledge" && source.citation);
  return (
    <>
      {llm && (
        <p className={styles.metadata}>
          {labels[llm.actual_provider]} · {llm.model}
          {llm.fallback_used && " · Đã chuyển nhà cung cấp"}
          {!!agents?.length &&
            ` · ${agents.map((agent) => labels[agent] ?? agent).join(", ")}`}
        </p>
      )}
      {!!sources?.length && (
        <details className={styles.sources}>
          <summary>Nguồn dữ liệu</summary>
          <ul>
            {sources.map((source) => {
              const key = `${source.kind}-${source.ref ?? ""}`;
              const citation = source.citation;
              if (source.kind === "knowledge" && citation) {
                return (
                  <li key={key}>
                    [{(citedSources?.indexOf(source) ?? -1) + 1}]{" "}
                    {citation.url?.startsWith("https://") ? (
                      <a href={citation.url} target="_blank" rel="noreferrer">
                        {citation.title}
                      </a>
                    ) : (
                      citation.title
                    )}
                    {` · ${citation.publisher} · ${citation.section} · ${citation.revision} (${citation.version})`}
                  </li>
                );
              }
              return (
                <li key={key}>
                  {labels[source.kind] ?? source.kind}
                  {source.timestamp &&
                    ` · ${dateTime.format(new Date(source.timestamp))}`}
                  {source.data_source &&
                    ` · ${labels[source.data_source] ?? source.data_source}`}
                  {source.model_version && ` · ${source.model_version}`}
                  {source.stale && " · Dữ liệu cũ"}
                </li>
              );
            })}
          </ul>
        </details>
      )}
    </>
  );
}

export function ChatView() {
  const { token } = usePlantcare();
  const [providerInfo, setProviderInfo] = useState<ProviderInfo | null>(null);
  const [providerHealth, setProviderHealth] = useState<
    Partial<Record<"cloud" | "local", boolean>>
  >({});
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [beforeTurn, setBeforeTurn] = useState<number | null>(null);
  const [mode, setMode] = useState<Mode>("cloud");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const attempt = useRef<{ key: string; id: string } | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const log = useRef<HTMLOListElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    const options = { signal: controller.signal };
    void Promise.all([
      request<ProviderInfo>("/chat/providers", token, options),
      request<ConversationPage>("/chat/conversations", token, options),
    ])
      .then(([providers, page]) => {
        if (controller.signal.aborted) return;
        setProviderInfo(providers);
        setMode(providers.default_mode);
        setConversations(page.items);
        setNextOffset(page.next_offset);
        setReady(true);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorText(failure));
      });
    void request<{ providers: typeof providerHealth }>(
      "/chat/health",
      token,
      options,
    )
      .then((data) => {
        if (!controller.signal.aborted) setProviderHealth(data.providers);
      })
      .catch(() => {});
    return () => controller.abort();
  }, [token]);

  function newConversation() {
    setConversation(null);
    setMessages([]);
    setBeforeTurn(null);
    setMode(providerInfo?.default_mode ?? "cloud");
    setDraft("");
    setError("");
    input.current?.focus();
  }

  async function selectConversation(item: Conversation) {
    setBusy(true);
    setError("");
    try {
      const page = await request<MessagePage>(
        `/chat/conversations/${item.id}/messages`,
        token,
      );
      setConversation(page.conversation);
      setMessages(page.items);
      setBeforeTurn(page.next_before_turn);
      setMode(page.conversation.default_mode);
      setDraft("");
      requestAnimationFrame(() =>
        log.current?.scrollTo({ top: log.current.scrollHeight }),
      );
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  async function loadMore() {
    if (nextOffset === null) return;
    setBusy(true);
    setError("");
    try {
      const page = await request<ConversationPage>(
        `/chat/conversations?offset=${nextOffset}`,
        token,
      );
      setConversations((current) => [
        ...new Map(
          [...current, ...page.items].map((item) => [item.id, item]),
        ).values(),
      ]);
      setNextOffset(page.next_offset);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  async function loadOlderMessages() {
    if (!conversation || beforeTurn === null) return;
    setBusy(true);
    setError("");
    try {
      const page = await request<MessagePage>(
        `/chat/conversations/${conversation.id}/messages?before_turn=${beforeTurn}`,
        token,
      );
      setMessages((current) => [...page.items, ...current]);
      setBeforeTurn(page.next_before_turn);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = draft.trim();
    if (!message || busy) return;
    setBusy(true);
    setSending(true);
    setError("");
    try {
      let selected = conversation;
      if (!selected) {
        selected = await request<Conversation>("/chat/conversations", token, {
          method: "POST",
          body: JSON.stringify({ default_mode: mode }),
        });
        setConversation(selected);
        setConversations((items) => [selected!, ...items]);
      }
      const key = JSON.stringify([selected.id, mode, message]);
      if (attempt.current?.key !== key)
        attempt.current = { key, id: crypto.randomUUID() };
      const turn = await request<Turn>(
        `/chat/conversations/${selected.id}/messages`,
        token,
        {
          method: "POST",
          signal: AbortSignal.timeout(55000),
          body: JSON.stringify({
            message,
            mode,
            request_id: attempt.current.id,
          }),
        },
      );
      setMessages((current) => [
        ...new Map(
          [...current, turn.user_message, turn.assistant_message].map(
            (item) => [item.id, item],
          ),
        ).values(),
      ]);
      setConversation(turn.conversation);
      setConversations((items) => [
        turn.conversation,
        ...items.filter((item) => item.id !== turn.conversation.id),
      ]);
      attempt.current = null;
      setDraft("");
      requestAnimationFrame(() =>
        log.current?.scrollTo({ top: log.current.scrollHeight }),
      );
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
      setSending(false);
    }
  }

  const unavailable = mode !== "auto" && providerHealth[mode] === false;
  return (
    <div className={styles.layout}>
      <aside className={`card ${styles.history}`} aria-label="Hội thoại">
        <div className={styles.historyHeading}>
          <h2>Hội thoại</h2>
          <button
            type="button"
            className="button secondary"
            onClick={newConversation}
            disabled={busy || !ready}
          >
            Hội thoại mới
          </button>
        </div>
        <ul>
          {conversations.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                disabled={busy}
                onClick={() => selectConversation(item)}
                aria-current={item.id === conversation?.id ? "true" : undefined}
              >
                {item.title}
              </button>
            </li>
          ))}
        </ul>
        {nextOffset !== null && (
          <button
            className="text-button"
            type="button"
            disabled={busy}
            onClick={loadMore}
          >
            Xem thêm hội thoại
          </button>
        )}
      </aside>
      <section
        className={`card ${styles.panel}`}
        aria-label="Trò chuyện với trợ lý"
      >
        <div className={styles.toolbar}>
          <h2>{conversation?.title ?? "Hỏi về vườn cà chua"}</h2>
          <label>
            Mô hình
            <select
              value={mode}
              disabled={busy || !ready}
              onChange={(event) => setMode(event.target.value as Mode)}
            >
              <option value="cloud">Cloud</option>
              <option value="local">Local</option>
              <option value="auto">Tự động</option>
            </select>
          </label>
        </div>
        {unavailable && (
          <p className={styles.notice} role="status">
            {mode === "cloud"
              ? "Cloud chưa sẵn sàng. Kiểm tra API key và quyền truy cập model."
              : "Local chưa sẵn sàng. Kiểm tra Ollama và model đã tải."}
          </p>
        )}
        {mode === "auto" && (
          <p className={styles.metadata}>
            Ưu tiên {labels[providerInfo?.auto_preference ?? "cloud"]}. Khi dùng
            Cloud, câu hỏi được gửi đến nhà cung cấp.
          </p>
        )}
        {!ready && !error && <p role="status">Đang mở trợ lý…</p>}
        {ready && !messages.length && (
          <div className={styles.suggestions} aria-label="Câu hỏi gợi ý">
            {questions.map((question) => (
              <button
                key={question}
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={() => {
                  setDraft(question);
                  input.current?.focus();
                }}
              >
                {question}
              </button>
            ))}
          </div>
        )}
        {beforeTurn !== null && (
          <button
            className="text-button"
            type="button"
            disabled={busy}
            onClick={loadOlderMessages}
          >
            Xem tin nhắn trước
          </button>
        )}
        {!!messages.length && (
          <ol ref={log} className={styles.messages} aria-live="polite">
            {messages.map((message) => (
              <li
                key={message.id}
                className={
                  message.role === "user" ? styles.user : styles.assistant
                }
              >
                <strong>{message.role === "user" ? "Bạn" : "Trợ lý"}</strong>
                <p>{message.content}</p>
                {message.role === "assistant" && (
                  <AnswerDetails details={message.details} />
                )}
              </li>
            ))}
          </ol>
        )}
        {sending && (
          <p role="status" className={styles.metadata}>
            Đang tìm dữ liệu và soạn câu trả lời…
          </p>
        )}
        {error && (
          <p role="alert" className={styles.error}>
            {error}
          </p>
        )}
        <form className={styles.compose} onSubmit={send}>
          <label htmlFor="chat-message">Câu hỏi của bạn</label>
          <textarea
            ref={input}
            id="chat-message"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            rows={3}
            maxLength={1200}
            disabled={busy || !ready}
          />
          <div className={styles.composeActions}>
            <span>Trợ lý có thể tư vấn từ dữ liệu hiện có.</span>
            <button
              className="button"
              disabled={!ready || busy || !draft.trim()}
              type="submit"
            >
              {sending ? "Đang gửi…" : "Gửi câu hỏi"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
