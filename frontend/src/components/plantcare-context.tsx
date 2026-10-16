"use client";
import {
  createContext,
  useContext,
  Dispatch,
  SetStateAction,
  RefObject,
} from "react";
import { Dashboard, Job } from "@/lib/api";

type PlantcareState = {
  data: Dashboard;
  token: string;
  apiOnline: boolean;
  busy: boolean;
  stale: boolean;
  pumpOn: boolean;
  duration: number;
  setDuration: Dispatch<SetStateAction<number>>;
  pump: (action: string) => Promise<void>;
  act: (action: () => Promise<unknown>, success: string) => Promise<void>;
  runWeather: () => Promise<void>;
  purpose: string;
  setPurpose: Dispatch<SetStateAction<string>>;
  file: File | null;
  setFile: Dispatch<SetStateAction<File | null>>;
  fileInput: RefObject<HTMLInputElement | null>;
  image: Job | undefined;
  setSelectedJob: Dispatch<SetStateAction<string | null>>;
};

export const PlantcareContext = createContext<PlantcareState | null>(null);
export function usePlantcare() {
  const state = useContext(PlantcareContext);
  if (!state) throw new Error("Trang phải nằm trong PlantcareContext");
  return state;
}
