"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { db } from "@/lib/firebase";
import { ref, onValue, set, serverTimestamp } from "firebase/database";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Eye, HandMetal, Thermometer, Mic, MicOff, LogOut } from "lucide-react";

export default function TeacherDashboard({ onLogout }) {
  const [selectedStudent, setSelectedStudent] = useState("s01");

  // Firebase data states
  const [engagement, setEngagement] = useState(null);
  const [signData, setSignData] = useState(null);
  const [environment, setEnvironment] = useState(null);
  const [transcript, setTranscript] = useState("");

  // Speech recognition
  const [isRecording, setIsRecording] = useState(false);
  const recognitionRef = useRef(null);
  const transcriptRef = useRef("");

  // Real-time listeners
  useEffect(() => {
    const engRef = ref(db, `engagement/${selectedStudent}`);
    const signRef = ref(db, `sign/${selectedStudent}`);
    const envRef = ref(db, `environment`); // Changed to global environment path

    const unsub1 = onValue(engRef, (snap) => {
      setEngagement(snap.val());
    });
    const unsub2 = onValue(signRef, (snap) => {
      setSignData(snap.val());
    });
    const unsub3 = onValue(envRef, (snap) => {
      setEnvironment(snap.val());
    });

    return () => {
      unsub1();
      unsub2();
      unsub3();
    };
  }, [selectedStudent]);

  // Push transcript to Firebase
  const pushTranscript = useCallback(
    (text) => {
      const tRef = ref(db, `transcription/${selectedStudent}`);
      if (!text.trim()) {
        set(tRef, null);
        return;
      }
      set(tRef, {
        text: text.trim(),
        timestamp: Date.now(),
      });
    },
    [selectedStudent]
  );

  const clearTranscript = useCallback(() => {
    setTranscript("");
    transcriptRef.current = "";
    pushTranscript("");
  }, [pushTranscript]);

  // Speech recognition setup
  const toggleRecording = () => {
    if (isRecording) {
      if (recognitionRef.current) {
        recognitionRef.current.stop();
        recognitionRef.current = null; // Detach reference to prevent restart
      }
      setIsRecording(false);
      return;
    }

    const SpeechRecognition =
      window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      alert("Speech recognition is not supported in this browser. Try Chrome.");
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onresult = (event) => {
      let finalText = "";
      for (let i = 0; i < event.results.length; i++) {
        finalText += event.results[i][0].transcript;
      }
      transcriptRef.current = finalText;
      setTranscript(finalText);
      pushTranscript(finalText);
    };

    recognition.onerror = (event) => {
      // 'network' errors happen frequently in some browsers due to silent timeouts or brief disconnects.
      // We ignore them here and let the `onend` handler restart the recognition automatically.
      if (event.error === "network") {
        console.warn("Speech network connection briefly dropped, attempting to restart...");
        return;
      }
      
      console.error("Speech error:", event.error);
      if (event.error !== "no-speech") {
        setIsRecording(false);
      }
    };

    recognition.onend = () => {
      // If the ref still points to this recognition instance, we didn't intentionally stop it.
      // We should restart it to keep continuous listening active.
      if (recognitionRef.current === recognition) {
        try {
          recognition.start();
        } catch (e) {
          if (e.name !== "InvalidStateError") {
            console.error("Restart failed, resetting state:", e);
            recognitionRef.current = null;
            setIsRecording(false);
          }
        }
      }
    };

    recognitionRef.current = recognition;
    recognition.start();
    setIsRecording(true);
  };

  // Engagement display logic
  const engStatus = engagement?.engagement_status || "Waiting...";
  const engScore = engagement?.engagement_score ?? 0;
  const isEngaged =
    engStatus === "ENGAGED" || engStatus.includes("Reading");

  // Sign display
  const signLabel = signData?.label || "No sign detected";
  const signConf = signData?.confidence ?? 0;

  // Environment display
  const temp = environment?.temp ?? "—";
  const humidity = environment?.humidity ?? "—";
  const acStatus = environment?.ac ?? "—";
  const acSetPoint = environment?.ac_setpoint ?? "—";
  const lightStatus = environment?.lighting ?? "—";

  return (
    <div className="min-h-screen bg-black p-4 md:p-8 text-zinc-200">
      {/* Header */}
      <header className="flex flex-col md:flex-row items-center justify-between gap-4 p-4 md:p-6 mb-8 rounded-xl bg-zinc-950 border border-zinc-800 shadow-sm">
        <div className="flex items-center gap-4">
          <h2 className="text-xl font-bold text-white tracking-tight">
            Smart Classroom
          </h2>
          <Badge variant="outline" className="bg-white/10 text-white border-white/20">
            Teacher
          </Badge>
        </div>
        <div className="flex items-center gap-4 w-full md:w-auto">
          <Select value={selectedStudent} onValueChange={setSelectedStudent}>
            <SelectTrigger className="w-[180px] bg-zinc-900 border-zinc-800">
              <SelectValue placeholder="Select Student" />
            </SelectTrigger>
            <SelectContent className="bg-zinc-900 border-zinc-800 text-zinc-200">
              <SelectItem value="s01">Student 01</SelectItem>
              <SelectItem value="s02">Student 02</SelectItem>
              <SelectItem value="s03">Student 03</SelectItem>
              <SelectItem value="s04">Student 04</SelectItem>
              <SelectItem value="s05">Student 05</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="ghost" onClick={onLogout} className="text-zinc-400 hover:text-white hover:bg-white/10">
            <LogOut className="w-4 h-4 mr-2" />
            Logout
          </Button>
        </div>
      </header>

      {/* Dashboard Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        
        {/* Engagement Panel */}
        <Card className="bg-zinc-950 border-zinc-800">
          <CardHeader className="flex flex-row items-center pb-2">
            <div className="p-2 mr-3 rounded-lg bg-emerald-500/10 text-emerald-400">
              <Eye className="w-5 h-5" />
            </div>
            <CardTitle className="text-base font-semibold text-zinc-300 uppercase tracking-wider">Engagement</CardTitle>
          </CardHeader>
          <CardContent>
            <div className={`text-4xl font-extrabold mb-2 ${isEngaged ? "text-emerald-400" : "text-red-400"}`}>
              {isEngaged ? "ENGAGED" : "NOT ENGAGED"}
            </div>
            <div className="flex items-center gap-2 mb-6 text-sm text-zinc-400">
              <div className={`w-2 h-2 rounded-full ${isEngaged ? "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]" : "bg-red-400 shadow-[0_0_8px_rgba(248,113,113,0.8)]"}`}></div>
              {engStatus}
            </div>
            
            <Progress value={engScore} className={`h-2 ${isEngaged ? "bg-emerald-950" : "bg-red-950"}`} indicatorClassName={isEngaged ? "bg-emerald-500" : "bg-red-500"} />
            <div className="mt-2 text-sm text-zinc-500 font-medium">Score: {engScore}%</div>
          </CardContent>
        </Card>

        {/* Sign Language Panel */}
        <Card className="bg-zinc-950 border-zinc-800">
          <CardHeader className="flex flex-row items-center pb-2">
            <div className="p-2 mr-3 rounded-lg bg-cyan-500/10 text-cyan-400">
              <HandMetal className="w-5 h-5" />
            </div>
            <CardTitle className="text-base font-semibold text-zinc-300 uppercase tracking-wider">Sign Language</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-4xl font-extrabold text-cyan-400 mb-6">
              {signData ? signLabel.toUpperCase() : (
                <span className="text-zinc-600 animate-pulse">Waiting...</span>
              )}
            </div>
            
            {signData ? (
              <div className="flex items-center gap-3">
                <Progress value={signConf * 100} className="h-1.5 bg-zinc-800 flex-1" indicatorClassName="bg-cyan-500" />
                <span className="text-xs text-zinc-500 w-10 text-right">{(signConf * 100).toFixed(0)}%</span>
              </div>
            ) : (
                <div className="h-1.5 w-full bg-zinc-800 rounded-full mt-7"></div>
            )}
            <div className="mt-3 text-sm text-zinc-500">Detected sign from student camera</div>
          </CardContent>
        </Card>

        {/* Environment Panel */}
        <Card className="bg-zinc-950 border-zinc-800">
          <CardHeader className="flex flex-row items-center pb-2">
            <div className="p-2 mr-3 rounded-lg bg-amber-500/10 text-amber-500">
              <Thermometer className="w-5 h-5" />
            </div>
            <CardTitle className="text-base font-semibold text-zinc-300 uppercase tracking-wider">Environment</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 mt-2">
            <div className="flex justify-between items-center pb-3 border-b border-zinc-800/60">
              <span className="text-zinc-400 text-sm flex items-center gap-2">Temperature / Humidity</span>
              <span className="font-semibold">{temp !== "—" ? <span className="text-amber-400">{temp}°C</span> : "—"} / {humidity !== "—" ? <span className="text-blue-400">{humidity}%</span> : "—"}</span>
            </div>
            <div className="flex justify-between items-center pb-3 border-b border-zinc-800/60">
              <span className="text-zinc-400 text-sm flex items-center gap-2">AC Status (Set: {acSetPoint !== "—" ? `${acSetPoint}°C` : "—"})</span>
              <Badge variant={acStatus === "ON" ? "default" : "secondary"} className={acStatus === "ON" ? "bg-amber-500/20 text-amber-400 hover:bg-amber-500/30" : "bg-zinc-800 text-zinc-400"}>
                {acStatus}
              </Badge>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-zinc-400 text-sm flex items-center gap-2">Lighting</span>
              <span className="font-semibold">{lightStatus}</span>
            </div>
          </CardContent>
        </Card>

        {/* Transcription Panel */}
        <Card className="bg-zinc-950 border-zinc-800 flex flex-col">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <div className="flex items-center">
              <div className="p-2 mr-3 rounded-lg bg-indigo-500/10 text-indigo-400">
                <Mic className="w-5 h-5" />
              </div>
              <CardTitle className="text-base font-semibold text-zinc-300 uppercase tracking-wider">Speech to Student</CardTitle>
            </div>
            {transcript && (
              <Button variant="ghost" size="sm" onClick={clearTranscript} className="text-zinc-500 hover:text-red-400 h-8 px-2 text-xs">
                Clear
              </Button>
            )}
          </CardHeader>
          <CardContent className="flex flex-col flex-1 h-full pt-2">
            <div className="bg-black border border-zinc-800 rounded-lg p-4 min-h-[120px] max-h-[160px] overflow-y-auto w-full mb-4 text-zinc-300 text-sm leading-relaxed">
              {transcript || (
                <span className="text-zinc-600 animate-pulse">
                  Click the button below to start speaking...
                </span>
              )}
            </div>
            
            <Button
              className={`w-full h-12 mt-auto text-sm font-medium transition-all ${
                isRecording 
                  ? "bg-red-500/10 text-red-500 border border-red-500/50 hover:bg-red-500/20 shadow-[0_0_15px_rgba(239,68,68,0.2)] animate-pulse" 
                  : "bg-zinc-800 text-zinc-300 hover:bg-zinc-700 hover:text-white border border-zinc-700"
              }`}
              onClick={toggleRecording}
            >
              {isRecording ? (
                <><MicOff className="w-4 h-4 mr-2" /> Stop Recording</>
              ) : (
                <><Mic className="w-4 h-4 mr-2" /> Start Speaking</>
              )}
            </Button>
          </CardContent>
        </Card>

      </div>
    </div>
  );
}
