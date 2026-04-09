"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getBots, triggerRun } from "@/lib/api";

interface Bot {
  id: string;
  name: string;
  description: string | null;
  queue_name: string | null;
  is_active: boolean;
}

export default function BotsPage() {
  const router = useRouter();
  const [bots, setBots] = useState<Bot[]>([]);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState<string | null>(null);

  useEffect(() => {
    getBots().then((r) => setBots(r.data)).finally(() => setLoading(false));
  }, []);

  async function handleTrigger(bot: Bot) {
    setTriggering(bot.id);
    try {
      await triggerRun(bot.id);
      router.push("/runs");
    } finally {
      setTriggering(null);
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Bots</h1>

      {loading ? (
        <p className="text-gray-500">Carregando...</p>
      ) : bots.length === 0 ? (
        <div className="bg-white rounded-xl shadow-sm p-10 text-center text-gray-400">
          Nenhum bot cadastrado ainda.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {bots.map((bot) => (
            <div key={bot.id} className="bg-white rounded-xl shadow-sm p-5 flex flex-col gap-3">
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="font-semibold text-gray-900">{bot.name}</h2>
                  {bot.description && (
                    <p className="text-sm text-gray-500 mt-0.5">{bot.description}</p>
                  )}
                </div>
                <span
                  className={`text-xs font-medium px-2 py-1 rounded-full ${
                    bot.is_active ? "bg-green-50 text-green-600" : "bg-gray-100 text-gray-400"
                  }`}
                >
                  {bot.is_active ? "Ativo" : "Inativo"}
                </span>
              </div>

              {bot.queue_name && (
                <p className="text-xs text-gray-400">Fila: {bot.queue_name}</p>
              )}

              <button
                onClick={() => handleTrigger(bot)}
                disabled={!bot.is_active || triggering === bot.id}
                className="mt-auto bg-blue-600 text-white text-sm font-medium rounded-lg px-4 py-2 hover:bg-blue-700 disabled:opacity-40 transition-colors"
              >
                {triggering === bot.id ? "Disparando..." : "Executar agora"}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
