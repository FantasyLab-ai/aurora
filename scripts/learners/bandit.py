import random, json, os
from datetime import datetime
from scripts.db import _connect

class ThompsonBandit:
    def __init__(self):
        self._con=_connect()

    def _get_arm(self, ctx:str, idx:int):
        cur=self._con.cursor()
        row=cur.execute("SELECT alpha,beta FROM bandit_arms WHERE context_key=? AND arm_index=?",(ctx,idx)).fetchone()
        if row: return {"alpha":row[0],"beta":row[1]}
        cur.execute("INSERT OR IGNORE INTO bandit_arms(context_key,arm_index,alpha,beta,updated_at) VALUES(?,?,?,?,?)",
                    (ctx,idx,1.0,1.0,datetime.utcnow().isoformat()))
        self._con.commit()
        return {"alpha":1.0,"beta":1.0}

    def select(self, context_key:str, candidates):
        samples=[]
        for i,_ in enumerate(candidates):
            arm=self._get_arm(context_key,i)
            samples.append((i, random.betavariate(arm["alpha"], arm["beta"])))
        best=max(samples, key=lambda x: x[1])[0]
        return best

    def update(self, context_key:str, idx:int, reward:float):
        # simple success threshold → Bernoulli reward
        succ = 1.0 if reward >= 1.0 else 0.0
        cur=self._con.cursor()
        cur.execute("SELECT alpha,beta FROM bandit_arms WHERE context_key=? AND arm_index=?",(context_key,idx))
        row=cur.fetchone()
        a,b = (row[0],row[1]) if row else (1.0,1.0)
        a += succ; b += (1.0 - succ)
        cur.execute("INSERT INTO bandit_arms(context_key,arm_index,alpha,beta,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(context_key,arm_index) DO UPDATE SET alpha=?, beta=?, updated_at=?",
                    (context_key,idx,a,b,datetime.utcnow().isoformat(), a,b,datetime.utcnow().isoformat()))
        self._con.commit()
