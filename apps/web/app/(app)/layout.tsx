import { AuthGate } from "@/components/AuthGate";

export default function AppLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <AuthGate>{children}</AuthGate>;
}
