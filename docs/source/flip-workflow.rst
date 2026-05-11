##############
FLIP workflow
##############

Once a user has access to FLIP, they can construct a project, add project members and execute an SQL query at each of the consortium Trusts to determine data cohort sizes.

.. figure:: assets/support/flip_walkthrough-cohort_query.drawio.png
   :align: center

   FLIP cohort query.

If a sufficient cohort of data can be utilised, the Model Developer will upload their training and validating algorithms to FLIP, along with any other collateral required for training and testing. The Model Developer will indicate which Trusts' data they require and 'stage' the project, awaiting approval from the Trusts that their data can be used for the project.

.. figure:: assets/support/flip_walkthrough-upload_collateral.drawio.png
   :align: center

   File uploads.

Once a FLIP administrator has approved the project, FLIP will execute the cohort query at each of the selected Trusts to determine the DICOM series associated with the cohort and begin to copy the images from the Trust PACS system to the local XNAT cache.

.. figure:: assets/support/flip_walkthrough-approve_project.drawio.png
   :align: center

   Approved project.

Once the DICOM series have been cached in the local XNAT in each Secure Enclave, the Model Developer will be notified and they can begin the optional process of enriching the data. All users associated with the FLIP project will be provided with XNAT accounts and will be able to log in locally and segment, align, label or otherwise enrich the data prior to providing it to the algorithm for training. Only those users in the original FLIP project will have access to the images in the XNAT repository.

.. figure:: assets/support/flip_walkthrough-enrich_images.drawio.png
   :align: center

   Image enrichment using XNAT.

Once all images have been prepared, the Model Developer will be able to initiate the training process. The uploaded files will be deployed out to each of the Trusts and the algorithm will be provided with a dataframe containing the details of the selected cohort. The algorithm will be able to inspect the dataframe and request images from the XNAT cache for training purposes. Any image processing performed during the training process can potentially be written back to the XNAT project for future training cycles.

.. figure:: assets/support/flip_walkthrough-start_training_A.drawio.png
   :align: center

   Training start.

Between training cycles, the weighted model will be sent back to the Central Hub to be aggregated and redistributed out to the workers.

Once all training cycles are completed, the final weighted model and any recorded metrics will be made available to the Model Developer through the FLIP UI.

.. figure:: assets/support/flip_walkthrough-finish_training.drawio.png
   :align: center

   Training finish.
