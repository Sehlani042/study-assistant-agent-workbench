import { useState, useCallback, useEffect } from "react";
import { getChatHistory, chatOnPage } from "@/lib/api";
import type { ChatTurn, Language, UserPayload, DocumentStatus } from "@/lib/types";
import { historyToTurns, isPageNotReadyError } from "@/lib/utils";

export function useChat({
    currentUser,
    documentId,
    pageNo,
    status,
    language,
    ensureLLMReady,
}: {
    currentUser: UserPayload | null;
    documentId: string | null;
    pageNo: number;
    status: DocumentStatus | null;
    language: Language;
    ensureLLMReady: () => boolean;
}) {
    const [chatInput, setChatInput] = useState("");
    const [chatTurns, setChatTurns] = useState<ChatTurn[]>([]);
    const [isAsking, setIsAsking] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const loadChatHistory = useCallback(async (docId: string, targetPage: number) => {
        try {
            const history = await getChatHistory(docId, targetPage, 100);
            setChatTurns(historyToTurns(history.items));
        } catch (err) {
            const message = err instanceof Error ? err.message : "加载历史问答失败";
            if (!message.includes("404")) {
                setError(message);
            }
            setChatTurns([]);
        }
    }, []);

    useEffect(() => {
        if (!currentUser || !documentId || !status || status.progress.total_pages <= 0) {
            setChatTurns([]);
            return;
        }

        const boundedPage = Math.min(Math.max(1, pageNo), status.progress.total_pages);
        void loadChatHistory(documentId, boundedPage);
    }, [currentUser, documentId, pageNo, status, loadChatHistory]);

    const onAsk = async () => {
        if (!documentId || !chatInput.trim()) return;
        if (!ensureLLMReady()) return;
        const question = chatInput.trim();
        setChatInput("");
        setChatTurns((prev) => [...prev, { role: "ask", text: question }]);

        try {
            setIsAsking(true);
            await chatOnPage(documentId, pageNo, question, language);
            await loadChatHistory(documentId, pageNo);
        } catch (err) {
            const message = err instanceof Error ? err.message : "提问失败";
            if (isPageNotReadyError(message)) {
                setError("当前页尚未准备完成，请稍后再提问。");
            } else {
                setError(message);
            }
        } finally {
            setIsAsking(false);
        }
    };

    const resetChatState = useCallback(() => {
        setChatInput("");
        setChatTurns([]);
        setError(null);
    }, []);

    return {
        chatInput,
        setChatInput,
        chatTurns,
        setChatTurns,
        isAsking,
        error,
        setError,
        onAsk,
        loadChatHistory,
        resetChatState,
    };
}
