import axios from "axios";
import { getSession } from "next-auth/react";

const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL,
});

api.interceptors.request.use(async (config) => {
  const session = await getSession();
  if (session?.accessToken) {
    config.headers.Authorization = `Bearer ${session.accessToken}`;
  }
  return config;
});

// --- Auth ---
export const login = (email: string, password: string) => {
  const form = new URLSearchParams();
  form.append("username", email);
  form.append("password", password);
  return axios.post(`${process.env.NEXT_PUBLIC_API_URL}/api/auth/login`, form);
};

// --- Bots ---
export const getBots = () => api.get("/api/bots/");
export const getBot = (id: string) => api.get(`/api/bots/${id}`);

// --- Runs ---
export const getRuns = (botId?: string) =>
  api.get("/api/runs/", { params: botId ? { bot_id: botId } : {} });
export const triggerRun = (botId: string) =>
  api.post("/api/runs/", { bot_id: botId, triggered_by: "manual" });
export const getRunLogs = (runId: string) =>
  api.get(`/api/runs/${runId}/logs`);

// --- Schedules ---
export const getSchedules = (botId?: string) =>
  api.get("/api/schedules/", { params: botId ? { bot_id: botId } : {} });
export const createSchedule = (botId: string, cron: string) =>
  api.post("/api/schedules/", { bot_id: botId, cron_expression: cron, is_active: true });
export const deleteSchedule = (id: string) =>
  api.delete(`/api/schedules/${id}`);

export default api;
