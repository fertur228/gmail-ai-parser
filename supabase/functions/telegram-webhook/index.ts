// Supabase Edge Function: Telegram webhook for inline callbacks (Calendar, Tasks, feedback).
// Secrets: TELEGRAM_BOT_TOKEN, SUPABASE_SERVICE_ROLE_KEY, GOOGLE_OAUTH_CLIENT_ID,
//         GOOGLE_OAUTH_CLIENT_SECRET, optional TELEGRAM_WEBHOOK_SECRET

import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.1";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type, x-telegram-bot-api-secret-token",
};

async function refreshAccessToken(
  refreshToken: string,
  clientId: string,
  clientSecret: string,
): Promise<string> {
  const body = new URLSearchParams({
    client_id: clientId,
    client_secret: clientSecret,
    refresh_token: refreshToken,
    grant_type: "refresh_token",
  });
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  const txt = await res.text();
  if (!res.ok) throw new Error(`OAuth refresh failed: ${txt}`);
  const j = JSON.parse(txt) as { access_token?: string };
  if (!j.access_token) throw new Error("No access_token in refresh response");
  return j.access_token;
}

function addOneDayYmd(ymd: string): string {
  const [y, mo, d] = ymd.split("-").map((x) => parseInt(x, 10));
  const n = Date.UTC(y, mo - 1, d + 1);
  return new Date(n).toISOString().slice(0, 10);
}

function buildCalendarEventBody(
  title: string,
  startIso: string,
  description: string,
): Record<string, unknown> {
  const raw = startIso.trim();
  if (!raw.includes("T") && raw.length <= 10) {
    const d = raw.slice(0, 10);
    return {
      summary: title,
      description,
      start: { date: d },
      end: { date: addOneDayYmd(d) },
    };
  }
  let s = raw.endsWith("Z") ? raw.replace(/Z$/, "+00:00") : raw;
  const startMs = Date.parse(s);
  if (Number.isNaN(startMs)) throw new Error("Invalid event start ISO");
  const endMs = startMs + 3600 * 1000;
  return {
    summary: title,
    description,
    start: { dateTime: new Date(startMs).toISOString() },
    end: { dateTime: new Date(endMs).toISOString() },
  };
}

async function calendarInsert(
  accessToken: string,
  title: string,
  startIso: string,
  description: string,
): Promise<void> {
  const body = buildCalendarEventBody(title, startIso, description);
  const res = await fetch(
    "https://www.googleapis.com/calendar/v3/calendars/primary/events",
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) throw new Error(await res.text());
}

async function getDefaultTaskListId(accessToken: string): Promise<string> {
  const res = await fetch(
    "https://tasks.googleapis.com/tasks/v1/users/@me/lists",
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
  if (!res.ok) throw new Error(await res.text());
  const j = await res.json() as { items?: Array<{ id: string }> };
  const items = j.items;
  if (!items?.length) throw new Error("No Google Task lists");
  return items[0].id;
}

async function tasksInsert(
  accessToken: string,
  title: string,
  notes: string,
): Promise<void> {
  const listId = await getDefaultTaskListId(accessToken);
  const res = await fetch(
    `https://tasks.googleapis.com/tasks/v1/lists/${listId}/tasks`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ title: title || "Задача", notes }),
    },
  );
  if (!res.ok) throw new Error(await res.text());
}

async function tgMethod(
  botToken: string,
  method: string,
  payload: Record<string, unknown>,
): Promise<void> {
  const r = await fetch(`https://api.telegram.org/bot${botToken}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const j = await r.json() as { ok?: boolean; description?: string };
  if (!r.ok || !j.ok) {
    console.error("Telegram API error", method, j);
  }
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  const secret = Deno.env.get("TELEGRAM_WEBHOOK_SECRET");
  if (secret) {
    const h = req.headers.get("x-telegram-bot-api-secret-token");
    if (h !== secret) {
      return new Response("Forbidden", { status: 403 });
    }
  }

  const botToken = Deno.env.get("TELEGRAM_BOT_TOKEN");
  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  const clientId = Deno.env.get("GOOGLE_OAUTH_CLIENT_ID");
  const clientSecret = Deno.env.get("GOOGLE_OAUTH_CLIENT_SECRET");

  if (
    !botToken || !supabaseUrl || !serviceKey || !clientId || !clientSecret
  ) {
    console.error("Missing env for telegram-webhook");
    return new Response("Server misconfigured", { status: 500 });
  }

  const supabase = createClient(supabaseUrl, serviceKey);

  try {
    const update = await req.json() as {
      callback_query?: {
        id: string;
        data?: string;
        from?: { id: number };
        message?: { chat: { id: number }; message_id: number; text?: string };
      };
    };

    const cq = update.callback_query;
    if (!cq?.data || cq.from == null || cq.message == null) {
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    const data = cq.data;
    const telegramId = cq.from.id;
    const chatId = cq.message.chat.id;
    const messageId = cq.message.message_id;

    const sep = data.indexOf("|");
    const prefix = sep >= 0 ? data.slice(0, sep) : data;
    const token = sep >= 0 ? data.slice(sep + 1) : "";

    const { data: userRow, error: userErr } = await supabase
      .from("user_config")
      .select("*")
      .eq("telegram_id", telegramId)
      .maybeSingle();

    if (userErr) console.error(userErr);

    if (!userRow) {
      await tgMethod(botToken, "answerCallbackQuery", {
        callback_query_id: cq.id,
        text: "Профиль не найден в БД",
        show_alert: true,
      });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    const { data: emailRow, error: emailErr } = await supabase
      .from("processed_emails")
      .select("*")
      .eq("callback_token", token)
      .maybeSingle();

    if (emailErr) console.error(emailErr);

    if (prefix === "up" || prefix === "dn") {
      if (!emailRow?.message_id) {
        await tgMethod(botToken, "answerCallbackQuery", {
          callback_query_id: cq.id,
          text: "Запись не найдена",
          show_alert: true,
        });
      } else {
        const interesting = prefix === "up";
        await supabase.from("processed_emails").update({
          is_interesting: interesting,
        }).eq("message_id", emailRow.message_id);
        await tgMethod(botToken, "answerCallbackQuery", {
          callback_query_id: cq.id,
          text: interesting ? "Сохранено: интересно" : "Сохранено: не интересно",
        });
        await tgMethod(botToken, "editMessageReplyMarkup", {
          chat_id: chatId,
          message_id: messageId,
          reply_markup: null,
        });
      }
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    if (!emailRow) {
      await tgMethod(botToken, "answerCallbackQuery", {
        callback_query_id: cq.id,
        text: "Запись не найдена",
        show_alert: true,
      });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    if (prefix === "cal") {
      if ((emailRow.category as string) !== "event") {
        await tgMethod(botToken, "answerCallbackQuery", {
          callback_query_id: cq.id,
          text: "Не событие",
          show_alert: true,
        });
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        });
      }
      const startIso = emailRow.event_datetime_iso as string | null;
      if (!startIso) {
        await tgMethod(botToken, "answerCallbackQuery", {
          callback_query_id: cq.id,
          text: "Нет даты для календаря",
          show_alert: true,
        });
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        });
      }
      const crt = userRow.calendar_refresh_token as string | null;
      if (!crt) {
        await tgMethod(botToken, "answerCallbackQuery", {
          callback_query_id: cq.id,
          text: "Нет calendar_refresh_token",
          show_alert: true,
        });
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        });
      }
      try {
        const at = await refreshAccessToken(crt, clientId, clientSecret);
        const title = (emailRow.subject as string)?.trim() || "Событие";
        const desc = (emailRow.summary as string)?.trim() || "";
        await calendarInsert(at, title, startIso, desc);
        await tgMethod(botToken, "answerCallbackQuery", {
          callback_query_id: cq.id,
          text: "Добавлено в календарь",
        });
        await tgMethod(botToken, "editMessageText", {
          chat_id: chatId,
          message_id: messageId,
          text: "✅ Добавлено в календарь!",
        });
      } catch (e) {
        console.error(e);
        await tgMethod(botToken, "answerCallbackQuery", {
          callback_query_id: cq.id,
          text: `Календарь: ${String(e)}`.slice(0, 200),
          show_alert: true,
        });
      }
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    if (prefix === "tsk") {
      const cat = emailRow.category as string;
      if (cat !== "task" && cat !== "info") {
        await tgMethod(botToken, "answerCallbackQuery", {
          callback_query_id: cq.id,
          text: "Кнопка только для task/info",
          show_alert: true,
        });
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        });
      }
      const trt = userRow.tasks_refresh_token as string | null;
      if (!trt) {
        await tgMethod(botToken, "answerCallbackQuery", {
          callback_query_id: cq.id,
          text: "Нет tasks_refresh_token",
          show_alert: true,
        });
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        });
      }
      try {
        const at = await refreshAccessToken(trt, clientId, clientSecret);
        const title = (emailRow.subject as string)?.trim() ||
          "Задача из почты";
        const notes = (emailRow.summary as string)?.trim() || "";
        await tasksInsert(at, title, notes);
        await tgMethod(botToken, "answerCallbackQuery", {
          callback_query_id: cq.id,
          text: "Добавлено в задачи",
        });
        await tgMethod(botToken, "editMessageText", {
          chat_id: chatId,
          message_id: messageId,
          text: "✅ Добавлено в Google Tasks!",
        });
      } catch (e) {
        console.error(e);
        await tgMethod(botToken, "answerCallbackQuery", {
          callback_query_id: cq.id,
          text: `Tasks: ${String(e)}`.slice(0, 200),
          show_alert: true,
        });
      }
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    await tgMethod(botToken, "answerCallbackQuery", {
      callback_query_id: cq.id,
    });
  } catch (e) {
    console.error(e);
  }

  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
});
