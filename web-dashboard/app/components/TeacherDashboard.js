"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { db } from "@/lib/firebase";
import { ref, onValue, set, serverTimestamp } from "firebase/database";

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
    const envRef = ref(db, `environment/${selectedStudent}`);

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
      if (!text.trim()) return;
      const tRef = ref(db, `transcription/${selectedStudent}`);
      set(tRef, {
        text: text.trim(),
        timestamp: Date.now(),
      });
    },
    [selectedStudent]
  );

  // Speech recognition setup
  const toggleRecording = () => {
    if (isRecording) {
      if (recognitionRef.current) {
        recognitionRef.current.stop();
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
      console.error("Speech error:", event.error);
      if (event.error !== "no-speech") {
        setIsRecording(false);
      }
    };

    recognition.onend = () => {
      // Restart if still recording
      if (isRecording) {
        try {
          recognition.start();
        } catch (e) {
          console.error("Restart failed:", e);
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
  const acStatus = environment?.ac ?? "—";
  const lightStatus = environment?.lighting ?? "—";

  return (
    <div className="dashboard-container">
      {/* Header */}
      <div className="dashboard-header">
        <div className="header-left">
          <h2>Smart Classroom</h2>
          <span className="header-badge badge-teacher">Teacher</span>
        </div>
        <div className="header-right">
          <select
            className="student-switcher"
            value={selectedStudent}
            onChange={(e) => setSelectedStudent(e.target.value)}
          >
            <option value="s01">Student 01</option>
            <option value="s02">Student 02</option>
            <option value="s03">Student 03</option>
            <option value="s04">Student 04</option>
            <option value="s05">Student 05</option>
          </select>
          <button className="logout-btn" onClick={onLogout}>
            ← Logout
          </button>
        </div>
      </div>

      {/* Dashboard Grid */}
      <div className="teacher-grid">
        {/* Engagement Panel */}
        <div className="glass-card panel">
          <div className="panel-header">
            <div className="panel-icon emerald">👁️</div>
            <span className="panel-title">Engagement</span>
          </div>
          <div
            className={`big-value ${isEngaged ? "engaged" : "not-engaged"}`}
          >
            {isEngaged ? "ENGAGED" : "NOT ENGAGED"}
          </div>
          <div className="sub-value">
            <span
              className={`status-dot ${isEngaged ? "green" : "red"}`}
            ></span>
            {engStatus}
          </div>
          <div className="score-bar-container">
            <div
              className="score-bar"
              style={{
                width: `${engScore}%`,
                background: isEngaged
                  ? "var(--accent-emerald)"
                  : "var(--accent-red)",
              }}
            />
          </div>
          <div className="sub-value" style={{ marginTop: 8 }}>
            Score: {engScore}%
          </div>
        </div>

        {/* Sign Language Panel */}
        <div className="glass-card panel">
          <div className="panel-header">
            <div className="panel-icon cyan">🤟</div>
            <span className="panel-title">Sign Language</span>
          </div>
          <div className="big-value sign">
            {signData ? signLabel.toUpperCase() : (
              <span className="shimmer">Waiting...</span>
            )}
          </div>
          {signData && (
            <div className="confidence-bar">
              <div className="confidence-track">
                <div
                  className="confidence-fill"
                  style={{ width: `${(signConf * 100).toFixed(0)}%` }}
                />
              </div>
              <span className="confidence-text">
                {(signConf * 100).toFixed(0)}%
              </span>
            </div>
          )}
          <div className="sub-value" style={{ marginTop: 8 }}>
            Detected sign from student camera
          </div>
        </div>

        {/* Environment Panel */}
        <div className="glass-card panel">
          <div className="panel-header">
            <div className="panel-icon amber">🌡️</div>
            <span className="panel-title">Environment</span>
          </div>
          <div className="env-row">
            <span className="env-label">
              ❄️ Air Conditioning
            </span>
            <span className="env-value">{acStatus}</span>
          </div>
          <div className="env-row">
            <span className="env-label">
              💡 Lighting
            </span>
            <span className="env-value">{lightStatus}</span>
          </div>
        </div>

        {/* Whisper / Transcription Panel */}
        <div className="glass-card panel">
          <div className="panel-header">
            <div className="panel-icon indigo">🎙️</div>
            <span className="panel-title">Speech to Student</span>
          </div>
          <div className="transcript-area">
            {transcript || (
              <span className="shimmer">
                Click the button below to start speaking...
              </span>
            )}
          </div>
          <button
            className={`mic-btn ${isRecording ? "recording" : ""}`}
            onClick={toggleRecording}
          >
            {isRecording ? "🔴 Stop Recording" : "🎤 Start Speaking"}
          </button>
        </div>
      </div>
    </div>
  );
}
