..  Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

.. _paper:

#########
The paper
#########

FLIP is described in
`Making Cross-Continental Federated Learning Repeatable with FLIP: a Multi-Application Study
<https://arxiv.org/abs/2609.36001>`_ (Garcia-Dias, Triay Bagur *et al.*, 2026). The study runs federated
fine-tuning and federated evaluation on chest X-ray cohorts across FLIP nodes in the UK and Thailand.

The guides below reproduce the paper's experiments on your own FLIP deployment:

- :doc:`paper/arkplus-fine-tuning` — the federated Ark+ chest X-ray classification app used in the study.
- :doc:`paper/reproducing-platform-overhead` — how much wall-clock time FLIP's orchestration adds to training,
  compared with running the same app locally.

.. toctree::
   :hidden:
   :maxdepth: 2

   paper/arkplus-fine-tuning
   paper/reproducing-platform-overhead

*************
Citing FLIP
*************

If you use FLIP in your research, please cite the paper:

.. code-block:: bibtex

   @misc{garciadias2026flip,
     title         = {Making Cross-Continental Federated Learning Repeatable with {FLIP}: a Multi-Application Study},
     author        = {Garcia-Dias, Rafael and {Triay Bagur}, Alexandre and Tangwiriyasakul, Chayanin and Fernandez, Virginia and
                      Esmaeili, Parhom and Ittichaiwong, Piyalitt and Li, Yang and Adams, Lawrence and Buncharoen, Wason and
                      Chapman, Martin and Chayanond, Benjamaporn and Chunrod, Sadthavud and Fongsri, Tanawat and
                      Gibson, Kass and Plungprasertkul, Supat and Tangpanithandee, Supawit and Veerakanjana, Kanyakorn and
                      Goh, Vicky and Antonelli, Michela and Zhang, Joe and Kespechara, Kongkiat and Ourselin, Sebastien and
                      Cardoso, M. Jorge},
     year          = {2026},
     eprint        = {2609.36001},
     archivePrefix = {arXiv},
     url           = {https://arxiv.org/abs/2609.36001}
   }
