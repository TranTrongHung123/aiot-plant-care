"use client";
import { Overview } from "@/components/overview";
import { usePlantcare } from "@/components/plantcare-context";
export default function Page() {
  const { data, apiOnline } = usePlantcare();
  return <Overview data={data} apiOnline={apiOnline} />;
}
