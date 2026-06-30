# How to create the population scheme

> 整體 db / pipeline 設計理念請見 [`db/README.md`](../README.md)。本目錄是其中 `pop`（IMV）+ `hidden`（母體表）生產層的建立流程。

1. For view with name '_xxx_list', create table in "list" schema. 

2. create table in list schema, add one row into it
from the dev view. 

3. create downstreams materialized views from '_xxx_list'
until the '_xxx_list' view

4. build cronjob auto adding row from 
'_xxx_list' materialized view into '_xxx_list' table

5. observe increase of each materialized view
