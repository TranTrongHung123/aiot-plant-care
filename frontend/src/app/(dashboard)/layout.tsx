import Home from "@/components/plantcare-app";
export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <Home>{children}</Home>;
}
