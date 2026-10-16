import type { Metadata } from "next";
import "./globals.css";
import "./workspace.css";

export const metadata: Metadata = {
  title: "Plantcare • AIoT Lab",
  description: "Giám sát cây cà chua, tưới mô phỏng và AI đã huấn luyện",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="vi">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
