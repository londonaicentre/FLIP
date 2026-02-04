<!--
    Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

# XNAT

## Overview

This is a very simple development environment to enable new users of XNAT to interact with the tool in a local environment, using Orthanc as as the mock PACS.

Extended from Haleema's repo <https://github.com/londonaicentre/xnat_explorer>.

## Setup

This repo uses a [Alex edit]: (modified) fork of the [xnat-docker-compose](https://github.com/NrgXnat/xnat-docker-compose) repository to set up XNAT, adds Orthanc container on a shared network.

[Alex edit] Had to change the ports in xnat-docker-compose/docker-compose.yml from the [xnat-docker-compose](https://github.com/NrgXnat/xnat-docker-compose)

Note you need Orthanc running in order to startup XNAT and configure it properly (see [orthanc](../orthanc/) )

Note you need to have started Orthanc and the APIs so that the Docker network exists.

### Download plugins

You will need to download the following plugins and place them in the `xnat/plugins` folder. Make sure to use the correct version of the plugins that are compatible the XNAT version. Check the plugins' compatibility matrix (for example, see [DQR Plugin Compatibility Matrix](https://wiki.xnat.org/xnat-tools/dqr-plugin-compatibility-matrix), [Container Service Compatibility Matrix](https://wiki.xnat.org/container-service/container-service-compatibility-matrix)).

You can use the following Makefile command to download the plugins. From the folder 'xnat', run:

```sh
make xnat-plugins-download
```

The following table lists the compatible versions of the plugins for the XNAT version we are using.

| Plugin                          | Compatible version for XNAT 1.9.1.1 |
|---------------------------------|-------------------------------------|
| Container Service Plugin        | [3.6.3](https://api.bitbucket.org/2.0/repositories/xnatdev/container-service/downloads/container-service-3.6.3-fat.jar)         |
| Batch Launch Plugin             | [0.7.1](https://api.bitbucket.org/2.0/repositories/xnatx/xnatx-batch-launch-plugin/downloads/batch-launch-0.7.1.jar)            |
| DICOM Query-Retrieve Plugin     | [2.2.0](https://api.bitbucket.org/2.0/repositories/xnatdev/dicom-query-retrieve/downloads/dicom-query-retrieve-2.2.0-xpl.jar)   |
| OHIF Viewer Plugin              | [3.7.1](https://xnat.org/files/ohif-viewer-xnat-plugin/ohif-viewer-3.7.1-fat.jar)                                               |

###  Steps

* Use the Makefile in this folder and run

    ```sh
    make xnat-reset # this will cleanup previous data, plugins and configurations
    make up
    ```

* Once XNAT is up, configure XNAT using the below (only needs to be run once). In a separate terminal, run:

    ```sh
    make xnat-configure
    ```

The `xnat-configure` command will configure XNAT by creating the service account, changing the default admin password, configuring the SCP receiver, registering PACS, etc. It will also configure dcm2niix in the container service plugin.

If successful, you will be able to log in to XNAT with the service account credentials.

## Importing data

Use the Imaging API to import data from Orthanc to XNAT.

This can also be done manually from XNAT:

* At the top of the homepage, select the **Browse > All Projects** tab.
* Open your project.
* Click on **Import from PACS** on the right-hand sidebar.
* Select **Orthanc** as your **Source PACS** (the selected SCP Receiver should default to **XNAT:8104**).
* [Query PACS](https://wiki.xnat.org/xnat-tools/using-dqr-searching-the-pacs-and-importing-data#UsingDQR:SearchingthePACSandImportingData-Querying/SearchingforImageSessions).

## Troubleshooting

### [`FIXED`] Issue with receiving data from PACS (Orthanc) -- 'Error: Cannot import' when going to 'Import Studies from PACS' in a project page

* Go to Administer > Site Administration > DICOM SCP Receivers > select XNAT > Change identifier to 'dqrObjectIdentifier' and enable custom processing.
* This was not being configured correctly

### [`FIXED`] Failed container launch: org.mandas.docker.client.exceptions.DockerRequestException: Request error: GET unix://localhost:80/images/json: 200

Container service plugin 3.4.3 is incompatible with Docker version 25 and above. This is a known issue (<https://groups.google.com/g/xnat_discussion/c/2kh3J-p_8bE>). See plugin release notes for 3.5.0 <https://bitbucket.org/xnatdev/container-service/src/master/CHANGELOG.md>. I was running Docker version 28.0.1, build 068a01e.

Fixed by installing newer version of the plugin (3.6.2). This meant installing a newer XNAT (1.9.1.1), after checking the compatibility matrix <https://wiki.xnat.org/container-service/container-service-compatibility-matrix#ContainerServiceCompatibilityMatrix-ContainerServiceCompatibilitywithXNAT>

Also installed newer Batch Launch Plugin and DQR Plugin.

### [`FIXED`] Container can't find data

Likely due to not having set path translation correctly -- this is now handled automatically.
