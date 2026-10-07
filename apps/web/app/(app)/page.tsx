import { redirect } from "next/navigation";

// O início do painel é o Dashboard (design-system.md 7.2).
export default function HomePage() {
  redirect("/dashboard");
}
