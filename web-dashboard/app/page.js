"use client";

import { useState } from "react";
import TeacherDashboard from "./components/TeacherDashboard";
import StudentDashboard from "./components/StudentDashboard";

export default function Home() {
  const [role, setRole] = useState(null); // 'teacher' | 'student'
  const [studentId, setStudentId] = useState("s01");
  const [loggedIn, setLoggedIn] = useState(false);

  if (!loggedIn) {
    return (
      <div className="login-container">
        <div className="glass-card login-box">
          <h1>Smart Classroom</h1>
          <p className="subtitle">Bio-Glove Monitoring Dashboard</p>

          <div className="role-selector">
            <button
              className={`role-btn ${role === "teacher" ? "active" : ""}`}
              onClick={() => setRole("teacher")}
            >
              <span className="role-icon">👩‍🏫</span>
              Teacher
            </button>
            <button
              className={`role-btn ${role === "student" ? "active" : ""}`}
              onClick={() => setRole("student")}
            >
              <span className="role-icon">🧑‍🎓</span>
              Student
            </button>
          </div>

          {role === "student" && (
            <select
              className="student-select"
              value={studentId}
              onChange={(e) => setStudentId(e.target.value)}
            >
              <option value="s01">Student 01</option>
              <option value="s02">Student 02</option>
              <option value="s03">Student 03</option>
              <option value="s04">Student 04</option>
              <option value="s05">Student 05</option>
            </select>
          )}

          <button
            className="enter-btn"
            disabled={!role}
            onClick={() => role && setLoggedIn(true)}
            style={{ opacity: role ? 1 : 0.5 }}
          >
            Enter Dashboard →
          </button>
        </div>
      </div>
    );
  }

  const handleLogout = () => {
    setLoggedIn(false);
    setRole(null);
  };

  if (role === "teacher") {
    return <TeacherDashboard onLogout={handleLogout} />;
  }

  return (
    <StudentDashboard studentId={studentId} onLogout={handleLogout} />
  );
}
