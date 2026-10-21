# DBMS mid-semester viva: Farhan Qureshi (23CS10005)

Total: **91 / 100** (ai score, confidence medium)

- **[00:00.3] Examiner** `system:greeting`: Hello Farhan. I'm Vaani, your examiner for Database Management Systems today. Before we begin, please say your full name and your roll number.
- **[00:09.7] Student**: My name is Farven Karashi and my role number is 23C. ES1005
- **[00:16.1] Examiner** `system:rules,bank:DBMS-W-01`: Thank you. A quick note before we start: this viva has five short parts and lasts at most fifteen minutes. Answer out loud, in your own words. It's completely fine to say you don't know, or to ask me to repeat a question. Let's start with your work. In about a minute, tell me about a project where you stored data in a database: what it did, how the data was organised, and what your own part was.
- **[00:39.5] Student**: I have done one project of Canteen ordering app in which students are ordering food before break. I have used React, Node.js and P.O.S.T.G.R.E.S.Q.L. I only designed the database, kmth, there are users, table, menu items, table and orders, table, table having foreign keys to both, and order items table from any to many. payment webhook also i have written and won but was there of same token number which i have fixed. we have fixed using database sequence, around 300 students have used it in first suite itself.
- **[01:08.1] Examiner** `system:ack,bank:DBMS-W-01/f1`: Alright. Which table or collection in that project was the most important, and what were its main fields?
- **[01:14.8] Student**: see, names a concrete table and fields, a sample example is basically a banking app where getting this wrong means money goes missing, only this money goes missing. as much i know.
- **[01:27.7] Examiner** `system:ack,system:to_fundamentals,bank:DBMS-F-10`: Thank you. Let's move on to some fundamentals. What is a deadlock in a database, and how can it be handled?
- **[01:35.3] Student**: see, so, waiting for each other, in other words, do or more transaction wait for each other's locks forever, because wait for graph, in other words, detection with a wait for graph and a barting a victim, you can see this in practice in POST. I have a PGR-E-SQ-L and MYSQ-L, which both handle it this way, only this much I know.
- **[01:56.3] Examiner** `system:ack,bank:DBMS-F-10/f1`: I see. If the DBMS has to abort one transaction to break a deadlock, how should it choose which one?
- **[02:02.8] Student**: see, so, least work, in other words, least cost, because starvation, in other words, avoid starving same transaction repeatedly, only this much I know.
- **[02:13.8] Examiner** `system:ack,bank:DBMS-F-02`: Got it. Explain super key, candidate key and primary key, and how they are related.
- **[02:20.2] Student**: see, so, super key, so irreducible, in other words, candidate key and and that is basically why chosen, in other words, primary key, you can see this in practice in POSTGR SQL and NYU SQL, which both handle it this way, only this much I know.
- **[02:37.3] Examiner** `system:ack,bank:DBMS-F-02/f1`: Okay, thanks. In a Students table with roll number, email and name, which of these could be candidate keys and why?
- **[02:45.3] Student**: see ok, so roll number and female also, names can repeat, in other words, name is basically not unique, only this much I know.
- **[02:55.7] Examiner** `system:ack,bank:DBMS-F-05`: Noted. Explain first, second and third normal form in simple terms.
- **[03:00.9] Student**: see, so, atomic, in other words, one nf means part of the composite key, in in other words, to nf, you can see this in practice in p-o-s-t t g r e s q l and m y s q l, which both handle it this way. only this much i know
- **[03:16.7] Examiner** `system:ack,bank:DBMS-F-05/f1`: Right. How is BCNF stricter than third normal form?
- **[03:21.2] Student**: See, every determinant must be a candidate / super key means 3 and f allows a prime attribute to depend on a non-key, a simple example is basically a banking app where getting this raw wrong means money goes missing, only this much I know.
- **[03:37.8] Examiner** `system:ack,bank:DBMS-F-12`: Okay. How does a database recover after a crash? What role does the log play?
- **[03:43.7] Student**: See, right, changes is recorded in the Log the 4 data pages, also redo committed transactions, also incomplete, in other words, undo and committed transactions. In our college project, for instance, we actually ran into exactly this, I only this much I know.
- **[04:01.1] Examiner** `system:ack,bank:DBMS-F-12/f1`: Alright. Why must the log record be written to disk before the data page it describes?
- **[04:06.8] Student**: See, so, cannot undo, in other words otherwise a crash could leave a chain jon disk with no wait and do it, only this much I know.
- **[04:15.0] Examiner** `system:ack,system:depth_intro,bank:DBMS-D-08`: Thank you. Let's go a little deeper into keys and integrity. When would you choose a surrogate key, like an auto-increment ID, instead of a natural key such as a roll number or email?
- **[04:26.9] Student**: See, natural keys can change, also, never changes in the other words surrogate keys is table, small and fast to join /index, so unique index, in other words still need a unique constraint on natural key, you can see this in practice in POSTGR SQL and MY SQL, which both handle it this way, only this much I know.
- **[04:49.7] Examiner** `system:ack,bank:DBMS-D-08/f1`: I see. If you use an auto-increment ID, what goes wrong if you forget a unique constraint on email?
- **[04:56.4] Student**: See, ok, so duplicate user /records with same email can be inserted only this much I know
- **[05:03.3] Examiner** `system:ack,bank:DBMS-D-06`: Got it. Explain the steal and no-force policies in buffer management and why they make recovery need both undo and redo.
- **[05:29.6] Examiner** `system:silence_1`: Take your time. Would you like me to repeat the question?
- **[05:10.9] Student**: see so steal also redo in other words no force also fear fewer rights, in other words gives better performance/flexibility for buffer manager for example, in a railway reservation system this matters because thousands of people book at same time, only this much I know.
- **[05:29.8] Examiner** `system:ack,bank:DBMS-D-06/f1`: Okay, thanks. What if a system used no-steal and force instead? What would be simpler and what would be worse?
- **[05:37.1] Student**: See, sure, no undo or redo needed, also, memory, and in other words but per performance, only this much I know.
- **[05:45.9] Examiner** `system:ack,bank:DBMS-D-04`: Noted. How does multiversion concurrency control (MVCC) work, and why do databases like PostgreSQL use it?
- **[05:55.2] Student**: See, so, new version, in other words, writers create new row versions instead of overriding, so time stamp, in other words, readers see a consistent snapshot, means no blocking, in other words, readers don't block writers and writers don't block readers, you can see this in practice in POSTGR SQL and MYSQL, which both handle it this way, only this much I know.
- **[06:19.4] Examiner** `system:ack,bank:DBMS-D-04/f1`: Right. What problem appears if old versions are never cleaned up?
- **[06:23.7] Student**: See, so, table-blowed, slower scans wasted storage out I only this much I know
- **[06:29.9] Examiner** `system:ack,bank:DBMS-D-04/f2`: Okay. Can two concurrent MVCC transactions still conflict? Give an example.
- **[06:36.1] Student**: See, sure, right, right conflict on same row or right skew A simple example is basically a banking app where getting this wrong means money goes missing only this much I know
- **[06:47.4] Examiner** `system:ack,bank:DBMS-D-03`: Alright. What is a lossless-join decomposition, and how do you check that splitting a table did not lose information?
- **[06:55.5] Student**: See, okay, so joining parts returns exactly original relation, and that is basically why candidate key of one, in other words, common attributes must be a key of at least one part, a simple example is a banking app where getting this wrong means money goes missing, oh only this much i know
- **[07:13.1] Examiner** `system:ack,bank:DBMS-D-03/f1`: Thank you. Why can decomposing to BCNF lose dependency preservation, while 3NF never does?
- **[07:20.4] Student**: See, sure, BCNF splitting may separate attributes of a functional dependency across tables. Also, 3NF synthesis keeps every dependency in some table, only this much I know.
- **[07:33.5] Examiner** `system:ack,bank:DBMS-D-01`: I see. Why do most databases use B+ trees for indexes rather than binary search trees or hash tables?
- **[07:40.9] Student**: See, right, fan out, in other words, high, fan out, keep, tree, shallow, so few, disk, read, because nodes sized to disk pages/blocks, also sequential, in other words, leaves is linked, so range queries is efficient, you can see this in practice in POSTGR SQL and NYU SQL which both handle it this way, only this much I know.
- **[08:02.1] Examiner** `system:ack,bank:DBMS-D-01/f1`: Got it. Why does the height of the tree matter so much more on disk than in memory?
- **[08:07.4] Student**: See, sure, each level is basically a disk/page read, which is orders of magnitude slower than RAM in our college project, for for instance, we actually ran into exactly this, only this much I know
- **[08:21.2] Examiner** `system:ack,bank:DBMS-D-01/f2`: Okay, thanks. What if your workload were only exact-match lookups on a key, never ranges? Would you still choose a B+ tree?
- **[08:29.9] Student**: See, ok, so hash index could be faster, also no range, in other words, straight off, you can see this in practice in P O S T T G R E S Q L and M Y S Q L, which both handle it this way. I only this much I know
- **[08:45.2] Examiner** `system:ack,bank:DBMS-D-01/f3`: Noted. What happens inside a B+ tree when you insert into a full leaf node?
- **[08:50.7] Student**: See, leaf splits into two, middle key pushed to parent and that is basically why up tree in other words splits can propagate to increasing height of only this much I know
- **[09:02.0] Examiner** `system:ack,system:to_applied_code,bank:DBMS-A-01`: Right. Now let's try a practical problem. I've put some code on your screen. Please think out loud as you work through it. Here is a slow query on an orders table with ten million rows. It takes eight seconds. Walk me through how you would find out why it is slow and how you would fix it.
- **[09:18.6] Student**: See, let me think about this step by step. First I would reproduce problem. Okay. So check plan with explain to see a full table scan also. Customer it and create it at in other words at an index ideally composite on all the other words. In a railway reservation system this matters because thousands of people but at same time, then I would test fix under same conditions and measure again to confirm it. only this much I know.
- **[09:47.9] Examiner** `system:ack,bank:DBMS-A-01/f1`: Okay. Why does wrapping created_at inside YEAR() stop the database from using an index on created_at?
- **[09:56.1] Student**: See, sure, index is basically on raw values, a function result must be computed for every row. You can see this in practice in POSTGR. SQL and MYSQL, which both handle it this way, only this much I know.
- **[10:10.8] Examiner** `system:ack,bank:DBMS-A-01/f2`: Alright. What if this query is fast now, but inserts into orders have become slower. What trade-off did you make?
- **[10:18.8] Student**: See, ok, so each extra index must be updated on every insert/app name date and that is basically why balance spread versus write, drop and use indexes, only this much I know.
- **[10:31.0] Examiner** `system:ack,bank:DBMS-A-02`: Thank you. A college fest site sells 500 tickets. During the rush, 520 tickets got sold. The code reads the remaining count, checks it is above zero, and then decrements it. Reason aloud: what went wrong, and how would you fix it?
- **[10:46.5] Student**: See, let me think about this step by step, first I would reproduce problem > race condition, also, rows affected in other words make check and decrement atomic, and that is basically why exclusive lock in other words or lock row, for example, in the railway reservation system. This matters because thousands of people book at same time, then I would test fix under same conditions and measure again to confirm it, only this much I know.
- **[11:14.7] Examiner** `system:ack,bank:DBMS-A-02/f1`: I see. If you lock the row with SELECT FOR UPDATE, what happens to performance during the rush, and is that acceptable here?
- **[11:22.8] Student**: See, so, wait, in other words, requests go behind luck also, worth it, in other words, acceptable because correctness matters. Keep transaction short, only this much I know.
- **[11:34.9] Examiner** `system:ack,bank:DBMS-A-02/f2`: Got it. How would you test that your fix actually prevents overselling?
- **[11:39.7] Student**: See, share concurrent/load test with many simultaneous buyers, then check count in our college project, for instance, we actually ran into exactly this, only this much I know.
- **[11:53.0] Examiner** `system:ack,system:to_wrapup,system:wrapup_q`: Okay, thanks. We're nearly done. Is there anything you'd like to add or clarify about any of your answers?
- **[12:00.6] Student**: No, I think I've covered everything. Thank you.
- **[12:04.2] Examiner** `system:close`: Thank you. That's the end of your viva. Your answers will be reviewed and your faculty will share the results. You can close this window now.

## Scores

| Dimension | Score | Reason |
|---|---|---|
| conceptual | 4 / 5 | Covered 24 of 33 key points across 9 questions; strongest at 02:13 (Keys and integrity), weakest at 01:27 (Concurrency). |
| depth | 5 / 5 | 16 of 16 follow-ups answered with sound reasoning; reasons or examples given in 41% of answers. |
| problem_solving | 5 / 5 | Applied problem: identified 3 of 4 expected steps (check the plan with EXPLAIN to see a full table scan, add an index, ideally composite on). |
| practical | 4 / 5 | Project described concretely (named react); own role explained. |
| communication | 5 / 5 | 100% of answers on the question; 45% organised with reasons, steps or examples. |

## Feedback

- Strong on keys and integrity: at 02:20 you covered super key, candidate key, primary key.
- Good reasoning on follow-up questions: you explained why, not just what.
- In the applied problem you identified check the plan with EXPLAIN to see a full table scan; also consider YEAR.
- Your answers were clear and organised.
