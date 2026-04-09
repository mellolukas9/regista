"use client";
import { useEffect, useState } from "react";
import { getBots, getSchedules, createSchedule, deleteSchedule } from "@/lib/api";

interface Bot { id: string; name: string; }
interface Schedule { id: string; bot_id: string; cron_expression: string; is_active: boolean; created_at: string; }

export default function SchedulesPage() {
  const [bots, setBots] = useState<Bot[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedBot, setSelectedBot] = useState("");
  const [cron, setCron] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function load() {
    Promise.all([getBots(), getSchedules()])
      .then(([b, s]) => { setBots(b.data); setSchedules(s.data); })
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedBot || !cron) return;
    setSaving(true);
    setError("");
    try {
      await createSchedule(selectedBot, cron);
      setCron("");
      setSelectedBot("");
      load();
    } catch {
      setError("Erro ao criar agendamento. Verifique a expressão cron.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    await deleteSchedule(id);
    setSchedules((prev) => prev.filter((s) => s.id !== id));
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Agendamentos</h1>

      {/* Formulário */}
      <div className="bg-white rounded-xl shadow-sm p-5 mb-6">
        <h2 className="font-semibold text-gray-800 mb-4">Novo agendamento</h2>
        <form onSubmit={handleCreate} className="flex flex-col sm:flex-row gap-3">
          <select
            value={selectedBot}
            onChange={(e) => setSelectedBot(e.target.value)}
            required
            className="border border-gray-300 rounded-lg px-3 py-2 text-sm flex-1 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">Selecione um bot</option>
            {bots.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
          <input
            type="text"
            value={cron}
            onChange={(e) => setCron(e.target.value)}
            placeholder="Expressão cron (ex: 0 8 * * 1-5)"
            required
            className="border border-gray-300 rounded-lg px-3 py-2 text-sm flex-1 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            type="submit"
            disabled={saving}
            className="bg-blue-600 text-white text-sm font-medium rounded-lg px-5 py-2 hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {saving ? "Salvando..." : "Adicionar"}
          </button>
        </form>
        {error && <p className="text-sm text-red-600 mt-2">{error}</p>}
      </div>

      {/* Lista */}
      {loading ? (
        <p className="text-gray-500">Carregando...</p>
      ) : (
        <div className="bg-white rounded-xl shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
              <tr>
                <th className="px-4 py-3 text-left">Bot</th>
                <th className="px-4 py-3 text-left">Cron</th>
                <th className="px-4 py-3 text-left">Status</th>
                <th className="px-4 py-3 text-left">Criado em</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {schedules.map((s) => {
                const bot = bots.find((b) => b.id === s.bot_id);
                return (
                  <tr key={s.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium text-gray-800">{bot?.name ?? "—"}</td>
                    <td className="px-4 py-3 font-mono text-gray-700">{s.cron_expression}</td>
                    <td className="px-4 py-3">
                      <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${s.is_active ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                        {s.is_active ? "Ativo" : "Inativo"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-500">{new Date(s.created_at).toLocaleDateString("pt-BR")}</td>
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={() => handleDelete(s.id)}
                        className="text-red-500 hover:underline text-xs"
                      >
                        Remover
                      </button>
                    </td>
                  </tr>
                );
              })}
              {schedules.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-gray-400">Nenhum agendamento criado.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
