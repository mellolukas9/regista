"use client";
import { useEffect, useState, useCallback } from "react";
import { getRuns, getBots, getRunLogs } from "@/lib/api";

interface Run {
  id: string;
  bot_id: string;
  status: string;
  triggered_by: string;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  prefect_flow_run_id: string | null;
}
interface Bot { id: string; name: string; }

const statusColor: Record<string, string> = {
  completed: "text-green-700 bg-green-50 border-green-200",
  running:   "text-blue-700 bg-blue-50 border-blue-200",
  failed:    "text-red-700 bg-red-50 border-red-200",
  pending:   "text-yellow-700 bg-yellow-50 border-yellow-200",
  cancelled: "text-gray-600 bg-gray-50 border-gray-200",
};

export default function RunsPage() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [bots, setBots] = useState<Bot[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [logs, setLogs] = useState<any[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);

  const load = useCallback(() => {
    Promise.all([getRuns(), getBots()])
      .then(([r, b]) => { setRuns(r.data); setBots(b.data); })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 10_000);
    return () => clearInterval(interval);
  }, [load]);

  async function openLogs(runId: string) {
    setSelectedRun(runId);
    setLogsLoading(true);
    setLogs([]);
    try {
      const res = await getRunLogs(runId);
      setLogs(res.data.logs ?? []);
    } finally {
      setLogsLoading(false);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Execuções</h1>
        <button onClick={load} className="text-sm text-blue-600 hover:underline">Atualizar</button>
      </div>

      {loading ? (
        <p className="text-gray-500">Carregando...</p>
      ) : (
        <div className="bg-white rounded-xl shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
              <tr>
                <th className="px-4 py-3 text-left">Bot</th>
                <th className="px-4 py-3 text-left">Status</th>
                <th className="px-4 py-3 text-left">Disparado por</th>
                <th className="px-4 py-3 text-left">Início</th>
                <th className="px-4 py-3 text-left">Duração</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {runs.map((run) => {
                const bot = bots.find((b) => b.id === run.bot_id);
                const duration =
                  run.started_at && run.finished_at
                    ? `${Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000)}s`
                    : run.started_at ? "em andamento" : "—";
                return (
                  <tr key={run.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium text-gray-800">{bot?.name ?? "—"}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium border ${statusColor[run.status] ?? ""}`}>
                        {run.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-500">{run.triggered_by}</td>
                    <td className="px-4 py-3 text-gray-500">
                      {run.created_at ? new Date(run.created_at).toLocaleString("pt-BR") : "—"}
                    </td>
                    <td className="px-4 py-3 text-gray-500">{duration}</td>
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={() => openLogs(run.id)}
                        className="text-blue-600 hover:underline text-xs"
                      >
                        Ver logs
                      </button>
                    </td>
                  </tr>
                );
              })}
              {runs.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-gray-400">Nenhuma execução ainda.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Modal de logs */}
      {selectedRun && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between px-5 py-4 border-b">
              <h2 className="font-semibold text-gray-900">Logs da execução</h2>
              <button onClick={() => setSelectedRun(null)} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
            </div>
            <div className="flex-1 overflow-auto p-4 bg-gray-900 rounded-b-xl font-mono text-xs text-green-400">
              {logsLoading ? (
                <p className="text-gray-400">Carregando logs...</p>
              ) : logs.length === 0 ? (
                <p className="text-gray-500">Sem logs disponíveis.</p>
              ) : (
                logs.map((log, i) => (
                  <div key={i} className="mb-1">
                    <span className="text-gray-500 mr-2">{log.timestamp?.slice(11, 19)}</span>
                    <span>{log.message}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
