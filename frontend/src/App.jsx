import { useCallback, useEffect, useRef, useState } from "react";
import AuthScreen, { claimSession, fetchCurrentUser, logoutUser } from "./Auth.jsx";
import HistoryDrawer from "./HistoryDrawer.jsx";

// 预设案例只提供快捷输入文字；实际追问、风险判断和结果都由后端决定。
const CASES = [
  {
    id: "01",
    title: "动态追问",
    detail: "从简短描述开始，逐步补充信息",
    message: "头部不适",
  },
  {
    id: "02",
    title: "信息足够",
    detail: "直接查看结构化整理结果",
    message: "头部不适两天，前额，程度轻，暂无其他不适，无病史",
  },
  {
    id: "03",
    title: "预设风险",
    detail: "体验规则优先的安全分流",
    message: "模拟人物突然呼吸困难",
  },
];

// 后端 SSE 事件里的节点名 → 用户可读的进度文案。
const NODE_LABELS = {
  intake: "提取关键信息",
  risk_check: "检查安全边界",
  decision: "规划下一步",
  tool: "检索知识库",
  followup: "生成追问",
  output: "整理结果",
  answer: "整理回答",
};

// 只对刚收到的回复做打字机效果；历史消息直接显示，避免刷新页面时整屏重播。
function AgentText({ text, animate, onReveal }) {
  const [shown, setShown] = useState(animate ? "" : text);
  const revealRef = useRef(onReveal);

  useEffect(() => {
    revealRef.current = onReveal;
  }, [onReveal]);

  useEffect(() => {
    if (!animate) {
      setShown(text);
      return;
    }
    let index = 0;
    setShown("");
    const timer = setInterval(() => {
      index += 2; // 每次两个字符，中文也能保持流畅
      setShown(text.slice(0, index));
      // 文本长高时保持聊天区贴底；每 10 次刷新一次，避免过于频繁。
      if (index % 10 === 0 || index >= text.length) revealRef.current?.();
      if (index >= text.length) {
        clearInterval(timer);
        // 最后一帧的 DOM 高度在本次回调后才稳定，补一次贴底。
        setTimeout(() => revealRef.current?.(), 60);
      }
    }, 18);
    return () => clearInterval(timer);
  }, [text, animate]);

  return (
    <>
      {shown}
      {animate && shown.length < text.length && <span className="caret" />}
    </>
  );
}

// 浏览器只缓存会话 ID 和保存时间。完整对话与问诊状态保存在后端数据库中。
const SESSION_KEY = "consultation_demo_session";
const SESSION_TTL = 30 * 60 * 1000;

// 页面刷新时尝试找回最近 30 分钟的会话，同时兼容旧版的缓存键名。
function loadSession() {
  try {
    const item = JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
    if (item?.id && Date.now() - item.savedAt < SESSION_TTL) return item.id;
  } catch {
    /* 无效缓存 */
  }
  localStorage.removeItem(SESSION_KEY);
  const legacyId = localStorage.getItem("session_id");
  const legacyTime = Number(localStorage.getItem("session_ts"));
  localStorage.removeItem("session_id");
  localStorage.removeItem("session_ts");
  if (legacyId && legacyTime && Date.now() - legacyTime < SESSION_TTL) {
    saveSession(legacyId);
    return legacyId;
  }
  return null;
}

// 每次成功请求后刷新保存时间，让仍在使用的会话继续保持可恢复。
function saveSession(id) {
  localStorage.setItem(SESSION_KEY, JSON.stringify({ id, savedAt: Date.now() }));
}
function clearSession() {
  localStorage.removeItem(SESSION_KEY);
}

// 统一处理 JSON 响应，并保留 HTTP 状态码供发送逻辑判断失效会话。
async function requestJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || "请求失败，请稍后重试");
    error.status = response.status;
    throw error;
  }
  return data;
}

// 首条消息先建空会话，再走 /stream；后续消息直接在已有会话上流式处理。
function createEmptySession() {
  return requestJson("/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ initial_message: "" }),
  });
}

// 把一帧 SSE 文本解析成 {event, data}；注释行是服务端心跳，直接跳过。
function parseFrame(frame) {
  let event = "message";
  let data = null;
  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) continue;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data = JSON.parse(line.slice(5).trim());
  }
  return data ? { event, data } : null;
}

// 读取 /stream 的 SSE 响应：进度事件交给 onNode，最终结果作为返回值。
async function streamMessage(sessionId, message, onNode) {
  const response = await fetch(
    "/sessions/" + encodeURIComponent(sessionId) + "/stream",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    },
  );
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const error = new Error(data.detail || "请求失败，请稍后重试");
    error.status = response.status;
    throw error;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      // 浏览器可能把 \r\n 拆在两个分片里，统一成 \n 后再找空行分隔。
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(
        /\r\n/g,
        "\n",
      );
      let index;
      while ((index = buffer.indexOf("\n\n")) >= 0) {
        const frame = parseFrame(buffer.slice(0, index));
        buffer = buffer.slice(index + 2);
        if (!frame) continue;
        if (frame.event === "node") {
          onNode?.(frame.data);
        } else if (frame.event === "result") {
          result = frame.data;
        } else if (frame.event === "error") {
          const error = new Error(frame.data.detail || "本轮处理失败，请重试");
          error.retryable = true;
          throw error;
        }
      }
    }
  } catch (streamError) {
    // 出错时主动断开连接；后端仍会把本轮跑完并落库。
    try {
      await reader.cancel();
    } catch {
      /* 连接可能已关闭 */
    }
    throw streamError;
  }

  if (!result) throw new Error("连接中断，请重试");
  return result;
}

export default function App() {
  // undefined = 正在确认登录态；null = 未登录；对象 = 已登录用户。
  const [user, setUser] = useState(undefined);
  // React state 会触发界面更新：会话 ID、消息、后端状态和输入框内容分别保存。
  const [sessionId, setSessionId] = useState(loadSession);
  const [messages, setMessages] = useState([]);
  const [state, setState] = useState(null);
  const [input, setInput] = useState("");
  const [activeCase, setActiveCase] = useState(null);
  // 场景卡点选后把案例作为输入框占位提示，用户直接回车即可发送。
  const [prefillCase, setPrefillCase] = useState(null);
  const [prefillMessage, setPrefillMessage] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState(Boolean(sessionId));
  const [error, setError] = useState("");
  // 流式期间的当前节点，以及刚收到、需要做打字机效果的消息下标。
  const [currentNode, setCurrentNode] = useState("");
  const [animatedIndex, setAnimatedIndex] = useState(-1);
  // 反馈提交状态："" → submitting → done / error
  const [feedbackStatus, setFeedbackStatus] = useState("");

  // ref 修改后不会触发渲染：busy 立即拦住重复点击，chatEnd 用于滚动到底部。
  const busy = useRef(false);
  const chatEnd = useRef(null);
  const composerRef = useRef(null);
  // 标记下一次发送要开启独立会话（场景卡预填时置位）。
  const freshNext = useRef(false);

  // 只滚聊天区，不滚页面；scrollIntoView 会把输入框顶出视口。
  const scrollChat = useCallback((behavior = "smooth") => {
    const container = chatEnd.current?.closest(".chat-stream");
    if (!container) return;
    container.scrollTo({ top: container.scrollHeight, behavior });
  }, []);

  // 先确认登录态；Cookie 无效时后端返回 401，前端展示登录页。
  useEffect(() => {
    let cancelled = false;
    fetchCurrentUser()
      .then((data) => {
        if (!cancelled) setUser(data);
      })
      .catch(() => {
        if (!cancelled) setUser(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 登录后把本机上的游客会话归到账号；失败不影响继续使用。
  useEffect(() => {
    if (!user || !sessionId) return;
    claimSession(sessionId).catch(() => {});
    // 只在登录状态变化时认领一次，之后由服务端记录归属。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  // 首次进入页面时，用缓存的 ID 从后端恢复完整会话。
  useEffect(() => {
    if (!user || !sessionId) return;
    let cancelled = false;
    requestJson("/sessions/" + encodeURIComponent(sessionId))
      .then((data) => {
        if (cancelled) return;
        setState(data);
        // 新接口返回完整 conversation；旧接口才需要用最近一条回复兜底。
        const legacyReply = data.rendered_text || data.asked_questions?.at(-1);
        setMessages(
          data.conversation?.length
            ? data.conversation
            : legacyReply
              ? [{ role: "agent", text: legacyReply }]
              : [],
        );
        saveSession(sessionId);
      })
      .catch(() => {
        if (cancelled) return;
        clearSession();
        setSessionId(null);
      })
      .finally(() => {
        if (!cancelled) setRestoring(false);
      });
    return () => {
      // 组件卸载后忽略尚未返回的请求，避免旧响应覆盖当前页面。
      cancelled = true;
    };
    // 仅在登录后恢复一次；之后使用每次请求的完整响应。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  // 新消息、选项或“正在整理信息”出现时，只滚动聊天区本身。
  useEffect(() => {
    scrollChat("auto");
  }, [messages, loading, state?.suggested_replies?.length, scrollChat]);

  // 所有 Hook 都已声明完毕，可以按登录态切换整页内容。
  if (user === undefined) {
    return (
      <div className="auth-splash" role="status">
        <span className="brand-mark">✳</span>
        <span>正在确认登录状态…</span>
      </div>
    );
  }
  if (!user) return <AuthScreen onAuthed={setUser} />;

  // 下列值都从后端状态推导，供页面展示，不参与后端问诊决策。
  const result = state?.final_result || {};
  const hasResult = Boolean(result.status);
  const isRisk = result.status === "risk_route";
  const facts = Object.entries(state?.collected_facts || {});
  const missing = state?.missing_fields || [];
  const suggestedReplies = state?.suggested_replies || [];
  // 进度条最多展示 3 次追问；真实次数仍以 state.followup_count 为准。
  const followupCount = Math.min(state?.followup_count || 0, 3);
  const phase = isRisk
    ? "风险分流"
    : hasResult
      ? "本轮已完成"
      : messages.length
        ? "等待补充"
        : "准备开始";

  // 点选场景：切到新的空对话视图，旧会话保留在历史里；案例只预填，不自动发送。
  function handlePickCase(item) {
    if (loading || restoring) return;
    freshNext.current = true;
    setActiveCase(item.title);
    setPrefillCase(item.title);
    setPrefillMessage(item.message);
    setInput("");
    setError("");
    clearSession();
    setSessionId(null);
    setState(null);
    setMessages([]);
    setCurrentNode("");
    setAnimatedIndex(-1);
    setFeedbackStatus("");
    requestAnimationFrame(() => {
      composerRef.current?.focus({ preventScroll: true });
      composerRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  // raw 是待发送文字；场景卡预填后由 freshNext 决定是否开启新会话。
  async function handleSend(raw = input) {
    // 输入框为空时回退到场景卡占位提示里的案例：回车就能直接发送。
    const message = raw.trim() || prefillMessage || "";
    // restoring 时还没确定旧会话状态；busy 在 React 重新渲染前就能阻止重复发送。
    if (!message || busy.current || restoring) return;
    busy.current = true;
    setLoading(true);
    setError("");
    setInput("");
    setPrefillCase(null);
    setPrefillMessage("");
    setCurrentNode("");
    setAnimatedIndex(-1);
    setFeedbackStatus("");
    const fromScenario = freshNext.current;
    freshNext.current = false;
    // 已有结果时继续发送，后端会回答追问；只有换场景或没有会话才新建。
    const startNew = fromScenario || !sessionId;
    const previousMessages = messages;
    const previousState = state;
    const previousSessionId = sessionId;
    // 先在界面显示用户消息，减少等待感；失败时会恢复原来的内容。
    if (startNew) {
      setMessages([{ role: "user", text: message }]);
      setState(null);
      if (!fromScenario) setActiveCase(null);
    } else {
      setMessages((items) => [...items, { role: "user", text: message }]);
    }

    // 流式进度：节点开始时更新提示文案，结束事件不改变界面。
    const onNode = (event) => {
      if (event.phase === "start") setCurrentNode(event.node);
    };

    let activeId = startNew ? null : sessionId;
    try {
      let data;
      if (startNew) {
        // 选择预设案例或重新开始后，不能把旧会话的风险状态带到新会话。
        activeId = (await createEmptySession()).session_id;
        setSessionId(activeId);
        saveSession(activeId);
        data = await streamMessage(activeId, message, onNode);
      } else {
        try {
          data = await streamMessage(activeId, message, onNode);
        } catch (requestError) {
          // 本地缓存对应的会话已不存在或已结束时，以当前输入创建新会话。
          if (requestError.status !== 404 && requestError.status !== 409)
            throw requestError;
          clearSession();
          activeId = (await createEmptySession()).session_id;
          setSessionId(activeId);
          saveSession(activeId);
          setActiveCase(null);
          data = await streamMessage(activeId, message, onNode);
        }
      }
      setSessionId(data.session_id);
      saveSession(data.session_id);
      setState(data);
      // 用后端返回的完整记录替换刚才临时显示的消息，保持前后端一致。
      setMessages(data.conversation || []);
      setAnimatedIndex(Math.max((data.conversation?.length || 1) - 1, 0));
    } catch (requestError) {
      // 流式连接失败时，后端可能已经把本轮跑完；先尝试从服务端恢复真实状态。
      let recovered = null;
      if (activeId) {
        try {
          recovered = await requestJson("/sessions/" + encodeURIComponent(activeId));
        } catch {
          /* 会话确实不可用，退回本地快照 */
        }
      }
      if (recovered) {
        setSessionId(activeId);
        saveSession(activeId);
        setState(recovered);
        setMessages(
          recovered.conversation?.length ? recovered.conversation : previousMessages,
        );
      } else {
        setSessionId(previousSessionId);
        setMessages(previousMessages);
        setState(previousState);
      }
      // 请求失败：把文字放回输入框，方便用户重试。
      setInput(message);
      setError(requestError.message || "连接失败，请稍后重试。");
    } finally {
      busy.current = false;
      setLoading(false);
      setCurrentNode("");
    }
  }

  // 只清空当前前端视图与本地缓存；后端会话保留，历史里还能找回。
  function handleReset() {
    if (busy.current || restoring) return;
    clearSession();
    setSessionId(null);
    setState(null);
    setMessages([]);
    setInput("");
    setActiveCase(null);
    setPrefillCase(null);
    setPrefillMessage("");
    freshNext.current = false;
    setError("");
    setFeedbackStatus("");
  }

  // 从历史列表打开会话：只恢复读取，不新建。
  async function openHistorySession(id) {
    if (busy.current || loading || restoring) return;
    setRestoring(true);
    setError("");
    try {
      const data = await requestJson("/sessions/" + encodeURIComponent(id));
      setSessionId(id);
      saveSession(id);
      setState(data);
      setMessages(data.conversation?.length ? data.conversation : []);
      setAnimatedIndex(-1);
      setActiveCase(null);
      setPrefillCase(null);
      setPrefillMessage("");
      freshNext.current = false;
      setFeedbackStatus("");
      setHistoryOpen(false);
      requestAnimationFrame(() => scrollChat("auto"));
    } catch {
      setError("会话加载失败，请稍后重试。");
    } finally {
      setRestoring(false);
    }
  }

  // 删除的是当前会话时，本地也要立刻清空，避免还显示已删除的内容。
  function handleHistoryDeleted(id) {
    if (id !== sessionId) return;
    clearSession();
    setSessionId(null);
    setState(null);
    setMessages([]);
    setInput("");
    setActiveCase(null);
    setPrefillCase(null);
    setPrefillMessage("");
    freshNext.current = false;
    setFeedbackStatus("");
  }

  // 退出登录：吊销令牌并清掉本地视图；历史记录留在账号里，下次登录还能看到。
  async function handleLogout() {
    if (busy.current) return;
    await logoutUser();
    clearSession();
    setSessionId(null);
    setState(null);
    setMessages([]);
    setInput("");
    setActiveCase(null);
    setPrefillCase(null);
    setPrefillMessage("");
    freshNext.current = false;
    setError("");
    setFeedbackStatus("");
    setHistoryOpen(false);
    setUser(null);
  }

  // 提交本轮反馈；同一会话同一轮重复提交由后端覆盖。
  async function submitFeedback(rating) {
    if (!sessionId || feedbackStatus === "sending" || feedbackStatus === "done") return;
    setFeedbackStatus("sending");
    try {
      await requestJson("/sessions/" + encodeURIComponent(sessionId) + "/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating }),
      });
      setFeedbackStatus("done");
    } catch {
      setFeedbackStatus("error");
    }
  }

  return (
    <div className="app-shell">
      {/* 顶部品牌与页面介绍是静态内容，不随会话变化。 */}
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">✳</span>
          <span>
            Consult<span className="brand-light">/Lab</span>
          </span>
        </div>
        <div className="topbar-account">
          <span className="topbar-meta">DAY 15 · INTERACTIVE DEMO</span>
          <button
            className="text-button"
            onClick={() => setHistoryOpen(true)}
            disabled={loading || restoring}
          >
            历史
          </button>
          <div className="user-chip">
            <span className="user-avatar" aria-hidden="true">
              {user.display_name.trim().charAt(0).toUpperCase() || "U"}
            </span>
            <span className="user-name" title={user.email}>
              {user.display_name}
            </span>
          </div>
          <button className="text-button" onClick={handleLogout}>
            退出
          </button>
        </div>
      </header>
      <main className="page-content">
        <section className="intro" aria-labelledby="page-title">
          <div className="eyebrow">
            <span className="eyebrow-dot" /> 模拟问诊工作台
          </div>
          <h1 id="page-title">
            让每一步问诊，
            <br />
            <span>都有迹可循。</span>
          </h1>
          <p>
            选择一个演示场景，或输入虚构病例。
            系统会展示追问、信息整理与预设风险分流的过程。
          </p>
          <div className="notice">
            <span aria-hidden="true">ⓘ</span>
            <span>仅限虚构病例演示，不提供真实医疗诊断、治疗或用药指导。</span>
          </div>
        </section>

        <section className="scenarios" aria-labelledby="scenario-title">
          <div className="section-heading">
            <div>
              <span className="section-kicker">QUICK START</span>
              <h2 id="scenario-title">选择演示场景</h2>
            </div>
            <span className="section-caption">每次选择都会开启独立会话</span>
          </div>
          <div className="scenario-grid">
            {/* 点选场景只预填输入框，发送时机交给用户。 */}
            {CASES.map((item) => (
              <button
                key={item.id}
                className={
                  "scenario-card" + (activeCase === item.title ? " selected" : "")
                }
                onClick={() => handlePickCase(item)}
                disabled={loading || restoring}
              >
                <span className="scenario-number">{item.id}</span>
                <span className="scenario-title">
                  {item.title}
                  <span className="scenario-arrow" aria-hidden="true">
                    ↗
                  </span>
                </span>
                <span className="scenario-description">{item.detail}</span>
              </button>
            ))}
          </div>
        </section>

        <section className="workspace" aria-label="问诊演示工作台">
          <div className="chat-panel">
            <div className="panel-header">
              <div>
                <span className="section-kicker">CONVERSATION</span>
                <h2>对话记录</h2>
              </div>
              <div className="panel-actions">
                <span className={"phase-pill" + (isRisk ? " phase-risk" : "")}>
                  <span className="phase-dot" />
                  {phase}
                </span>
                <button
                  className="text-button"
                  onClick={handleReset}
                  disabled={loading || restoring}
                >
                  重新开始
                </button>
              </div>
            </div>
            {/* role="log" 和 aria-live 让辅助技术读到新增的对话内容。 */}
            <div
              className="chat-stream"
              role="log"
              aria-live="polite"
              aria-relevant="additions text"
            >
              {/* 恢复中、空会话和已有消息分别显示不同内容。 */}
              {restoring ? (
                <div className="empty-chat">正在恢复会话…</div>
              ) : messages.length === 0 ? (
                <div className="empty-chat">
                  <div className="empty-icon">✳</div>
                  <h3>从一个模拟案例开始</h3>
                  <p>上方选择场景，或在下方输入虚构人物的症状描述。</p>
                </div>
              ) : (
                messages.map((item, index) => (
                  <div
                    className={
                      "message-row " +
                      (item.role === "user" ? "from-user" : "from-agent")
                    }
                    key={index}
                  >
                    <div
                      className={
                        "avatar " +
                        (item.role === "user" ? "user-avatar" : "agent-avatar")
                      }
                    >
                      {item.role === "user" ? "我" : "✳"}
                    </div>
                    <div className="message-content">
                      <div className="message-label">
                        {item.role === "user" ? "模拟用户" : "问诊 Agent"}
                      </div>
                      {/* 只有真正的风险结果消息才用风险样式，后续追问不再套用。 */}
                      <div
                        className={
                          "message-bubble" +
                          (isRisk &&
                          result.risk_message &&
                          item.role === "agent" &&
                          item.text.includes(result.risk_message)
                            ? " risk-bubble"
                            : "")
                        }
                      >
                        <AgentText
                          text={item.text}
                          animate={item.role === "agent" && index === animatedIndex}
                          onReveal={() => scrollChat("auto")}
                        />
                      </div>
                    </div>
                  </div>
                ))
              )}
              {/* 追问的快捷回答：点一下直接发送，输入框仍可自由输入。 */}
              {!loading && !hasResult && suggestedReplies.length > 0 && (
                <div className="reply-chips">
                  {suggestedReplies.map((reply) => (
                    <button
                      key={reply}
                      className="reply-chip"
                      onClick={() => handleSend(reply)}
                      disabled={loading || restoring}
                    >
                      {reply}
                    </button>
                  ))}
                </div>
              )}
              {/* 等待接口响应时显示临时提示，收到结果后由真实对话替换。 */}
              {loading && (
                <div className="message-row from-agent">
                  <div className="avatar agent-avatar">✳</div>
                  <div className="message-content">
                    <div className="message-label">问诊 Agent</div>
                    <div className="message-bubble typing">
                      正在{NODE_LABELS[currentNode] || "整理信息"}…
                    </div>
                  </div>
                </div>
              )}
              <div ref={chatEnd} />
            </div>
            <div className="composer-wrap">
              {/* 预填提示：告诉用户案例已经放进输入框，发送与否由自己决定。 */}
              {prefillCase && !input.trim() && !loading && (
                <div className="case-prefill-note">
                  <span aria-hidden="true">✎</span>
                  已选择「{prefillCase}」案例，直接发送即可，或输入自己的描述。
                </div>
              )}
              {/* 错误在输入框上方提示；会话结束后说明下一次发送会自动重开。 */}
              {error && (
                <div className="request-error" role="alert">
                  {error}
                </div>
              )}
              {hasResult && (
                <div className="new-session-note">
                  本轮已结束，可以继续追问；输入药品名称可查 OTC 说明书。
                </div>
              )}
              <div className="composer">
                <textarea
                  ref={composerRef}
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={(event) => {
                    // Enter 发送；Shift+Enter 换行；中文输入法组字时不误发送。
                    if (
                      event.key === "Enter" &&
                      !event.shiftKey &&
                      !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      handleSend();
                    }
                  }}
                  placeholder={prefillMessage || "描述一个虚构病例，或补充上一轮回答…"}
                  aria-label="输入模拟病例描述"
                  rows={2}
                  disabled={loading || restoring}
                />
                <button
                  className="send-button"
                  onClick={() => handleSend()}
                  disabled={(!input.trim() && !prefillMessage) || loading || restoring}
                  aria-label="发送消息"
                >
                  发送 <span aria-hidden="true">↗</span>
                </button>
              </div>
              <div className="composer-hint">Enter 发送 · Shift + Enter 换行</div>
            </div>
          </div>

          {/* 右侧面板只读取后端返回的状态，展示追问进度和整理结果。 */}
          <aside className="insight-panel" aria-label="会话状态与结果">
            <div className="insight-header">
              <span className="section-kicker">LIVE STATE</span>
              <h2>当前会话</h2>
              <p>信息会随每轮回答更新，并保存在本地会话库中。</p>
            </div>
            <div className="progress-block">
              <div className="progress-title">
                <span>追问进度</span>
                <strong>
                  {followupCount}
                  <span> / 3</span>
                </strong>
              </div>
              <div className="progress-track">
                <span style={{ width: (followupCount / 3) * 100 + "%" }} />
              </div>
              <p>每次只提出一个问题，最多追问三次。</p>
            </div>
            <div className="insight-section">
              <h3>
                已收集信息 <span>{facts.length}</span>
              </h3>
              {facts.length ? (
                <div className="fact-list">
                  {facts.map(([field, values]) => (
                    <div className="fact-item" key={field}>
                      <span>{field}</span>
                      <strong>{values.join("、")}</strong>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="muted">开始演示后，这里会显示已识别的信息。</p>
              )}
            </div>
            <div className="insight-section">
              <h3>
                尚待确认 <span>{missing.length}</span>
              </h3>
              {missing.length ? (
                <div className="chip-list">
                  {missing.map((field) => (
                    <span className="chip" key={field}>
                      {field}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="muted">暂无待确认字段。</p>
              )}
            </div>
            {/* 有风险标记时单独提示；最终结果只展示后端明确提供的字段。 */}
            {state?.risk_flags?.length > 0 && (
              <div className="risk-alert">
                <span className="risk-alert-label">预设规则已触发</span>
                <strong>{state.risk_flags.join("、")}</strong>
                <p>本轮已转入固定风险提示。</p>
              </div>
            )}
            {hasResult && (
              <div className={"result-summary" + (isRisk ? " result-risk" : "")}>
                <span className="section-kicker">SESSION RESULT</span>
                <h3>
                  {isRisk
                    ? "风险提示"
                    : result.status === "completed"
                      ? "整理结果"
                      : "处理状态"}
                </h3>
                {result.next_step && <p className="result-next">{result.next_step}</p>}
                {result.risk_message && <p>{result.risk_message}</p>}
                {result.tools_used?.length > 0 && (
                  <div className="result-tools">
                    已调用工具：{result.tools_used.join("、")}
                  </div>
                )}
                {feedbackStatus === "done" ? (
                  <p className="feedback-done">已收到反馈，谢谢</p>
                ) : (
                  <div className="feedback-row">
                    <button
                      className="feedback-button"
                      onClick={() => submitFeedback("helpful")}
                      disabled={feedbackStatus === "sending"}
                    >
                      有帮助
                    </button>
                    <button
                      className="feedback-button"
                      onClick={() => submitFeedback("unhelpful")}
                      disabled={feedbackStatus === "sending"}
                    >
                      没帮助
                    </button>
                    {feedbackStatus === "error" && (
                      <span className="feedback-error">提交失败，稍后重试</span>
                    )}
                  </div>
                )}
              </div>
            )}
          </aside>
        </section>
        <footer>
          CONSULT / LAB <span>·</span> 虚构病例交互演示
          <span className="build-tag">BUILD {__BUILD_ID__}</span>
        </footer>
      </main>
      <HistoryDrawer
        open={historyOpen}
        currentSessionId={sessionId}
        onClose={() => setHistoryOpen(false)}
        onOpen={openHistorySession}
        onDeleted={handleHistoryDeleted}
      />
    </div>
  );
}
