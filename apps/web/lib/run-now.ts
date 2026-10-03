"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { botsKey } from "@/lib/bots";
import { jobsKey, type JobDetail } from "@/lib/jobs";
import { machinesKey } from "@/lib/machines";
import { messageFor } from "@/lib/messages";

/** "Executar agora": cria a execução e avisa com um atalho para ela (design-system.md §9 e §14). */
export function useRunNow() {
  const queryClient = useQueryClient();
  const router = useRouter();
  return useMutation({
    mutationFn: (botId: string) => api.post<JobDetail>("/jobs", { bot_id: botId, params: {} }),
    onSuccess: (job) => {
      toast.success("Execução criada", {
        action: { label: "Ver execução", onClick: () => router.push(`/runs/${job.id}`) },
      });
      queryClient.invalidateQueries({ queryKey: jobsKey });
      queryClient.invalidateQueries({ queryKey: botsKey });
      queryClient.invalidateQueries({ queryKey: machinesKey });
    },
    onError: (error) => toast.error(messageFor(error), { duration: Infinity, closeButton: true }),
  });
}
