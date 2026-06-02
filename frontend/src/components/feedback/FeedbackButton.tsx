import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { submitFeedback } from "../../api/feedback";

const CATEGORIES = ["Bug", "Suggestion", "UX", "Other"] as const;

export default function FeedbackButton() {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [category, setCategory] = useState<typeof CATEGORIES[number]>("Suggestion");
  const [sent, setSent] = useState(false);
  const qc = useQueryClient();

  const mut = useMutation({
    mutationFn: () =>
      submitFeedback({
        message,
        category,
        page_url: typeof window !== "undefined" ? window.location.href : null,
      }),
    onSuccess: () => {
      setSent(true);
      setMessage("");
      qc.invalidateQueries({ queryKey: ["feedback"] });
      setTimeout(() => {
        setSent(false);
        setOpen(false);
      }, 1500);
    },
  });

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-6 right-6 z-40 bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium px-4 py-2.5 rounded-full shadow-lg shadow-blue-900/40 transition-colors"
        title="Send feedback to Abdulrahman"
      >
        💬 Feedback
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-md shadow-2xl">
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800">
              <p className="text-white font-semibold">Send feedback</p>
              <button
                onClick={() => setOpen(false)}
                className="text-gray-500 hover:text-gray-300 text-lg leading-none"
              >
                ✕
              </button>
            </div>

            <div className="px-5 py-4 space-y-3">
              <div>
                <label className="text-[10px] uppercase tracking-wider text-gray-500 block mb-1">
                  Category
                </label>
                <div className="flex gap-2 flex-wrap">
                  {CATEGORIES.map((c) => (
                    <button
                      key={c}
                      onClick={() => setCategory(c)}
                      className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${
                        category === c
                          ? "bg-blue-600 text-white"
                          : "bg-gray-800 text-gray-400 hover:text-gray-200"
                      }`}
                    >
                      {c}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-[10px] uppercase tracking-wider text-gray-500 block mb-1">
                  Message
                </label>
                <textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  rows={6}
                  placeholder="What's broken, what's confusing, or what's missing?"
                  className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder:text-gray-600 focus:outline-none focus:border-gray-600 resize-none"
                />
              </div>

              <p className="text-[10px] text-gray-600">
                Sent with the current page URL so Abdulrahman has context.
              </p>

              {mut.isError && (
                <p className="text-xs text-red-400">Failed to send — try again.</p>
              )}
              {sent && (
                <p className="text-xs text-green-400">Thanks — sent ✓</p>
              )}
            </div>

            <div className="flex items-center justify-between px-5 py-4 border-t border-gray-800">
              <button
                onClick={() => setOpen(false)}
                className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => mut.mutate()}
                disabled={mut.isPending || !message.trim() || sent}
                className="px-5 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {mut.isPending ? "Sending…" : sent ? "Sent ✓" : "Send"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
