import "./globals.css";

export const metadata = {
  title: "Smart Classroom Dashboard",
  description: "Real-time monitoring dashboard for Bio-Glove smart classroom system",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
