"use client";
import { useEffect, useState } from "react";
import { getBots, getRuns } from "@/lib/api";

interface Bot { id: string; name: string; is_active: boolean; }
interface Run { id: string; bot_id: string; status: string; created_at: string; triggered_by: string; }

const statusColor: Record<string, string> = {
  completed: "text-green-600 bg-green-50",
  running:   "text-blue-600 bg-blue-50",
  failed:    "text-red-600 bg-red-50",
  pending:   "text-yellow-600 bg-yellow-50",
  cancelled: "text-gray-600 bg-gray-50",
};

export default function DashboardPage() {
  const [bots, setBots] = useState<Bot[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getBots(), getRuns()])
      .then(([b, r]) => { setBots(b.data); setRuns(r.data); })
      .finally(() => setLoading(false));
  }, []);

  const totalBots   = bots.length;
  const activeBots  = bots.filter((b) => b.is_active).length;
  const todayRuns   = runs.filter((r) => r.created_at?.startsWith(new Date().toISOString().slice(0, 10))).length;
  const failedRuns  = runs.filter((r) => r.status === "failed").length;

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Dashboard</h1>

      {loading ? (
        <p className="text-gray-500">Carregando...</p>
      ) : (
        <>
          {/* Cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
            {[
              { label: "Total de Bots",   value: totalBots,  color: "bg-blue-600" },
              { label: "Bots Ativos",     value: activeBots, color: "bg-green-600" },
              { label: "Execuções Hoje",  value: todayRuns,  color: "bg-purple-600" },
              { label: "Falhas Recentes", value: failedRuns, color: "bg-red-600" },
            ].map((card) => (
              <div key={card.label} className="bg-white rounded-xl shadow-sm p-5">
                <p className="text-sm text-gray-500">{card.label}</p>
                <p className={`text-3xl font-bold mt-1 ${card.color.replace("bg-", "text-")}`}>
                  {card.value}
                </p>
              </div>
            ))}
          </div>

          {/* Últimas execuções */}
          <div className="bg-white rounded-xl shadow-sm">
            <div className="px-6 py-4 border-b border-gray-100">
              <h2 className="font-semibold text-gray-800">Últimas Execuções</h2>
            </div>
            <div className="divide-y divide-gray-50">
              {runs.slice(0, 10).map((run) => {
                const bot = bots.find((b) => b.id === run.bot_id);
                return (
                  <div key={run.id} className="px-6 py-3 flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-gray-800">{bot?.name ?? "Bot"}</p>
                      <p className="text-xs text-gray-400">
                        {new Date(run.created_at).toLocaleString("pt-BR")} · {run.triggered_by}
                      </p>
                    </div>
                    <span className={`text-xs font-medium px-2 py-1 rounded-full ${statusColor[run.status] ?? "bg-gray-100"}`}>
                      {run.status}
                    </span>
                  </div>
                );
              })}
              {runs.length === 0 && (
                <p className="px-6 py-4 text-sm text-gray-400">Nenhuma execução ainda.</p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
