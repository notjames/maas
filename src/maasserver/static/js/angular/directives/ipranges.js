/* Copyright 2016 Canonical Ltd.  This software is licensed under the
 * GNU Affero General Public License version 3 (see the file LICENSE).
 *
 * IP Ranges directive.
*/

angular.module('MAAS').directive('maasIpRanges', [
    '$filter', 'IPRangesManager', 'UsersManager',
    'ManagerHelperService', function(
        $filter, IPRangesManager, UsersManager, ManagerHelperService) {
        return {
            restrict: "E",
            scope: {
                obj: "="
            },
            templateUrl: (
                'static/partials/ipranges.html?v=' + (
                    MAAS_config.files_version)),
            controller: function($scope, $rootScope, $element, $document) {
                $scope.loading = true;
                $scope.ipranges = IPRangesManager.getItems();
                $scope.iprangeManager = IPRangesManager;
                $scope.newRange = null;
                $scope.editIPRange = null;
                $scope.deleteIPRange = null;

                // Return true if the authenticated user is super user.
                $scope.isSuperUser = function() {
                    return UsersManager.isSuperUser();
                };

                // Called to start adding a new IP range.
                $scope.addRange = function(type) {
                    $scope.newRange = {
                        type: type,
                        subnet: $scope.obj.id,
                        start_ip: "",
                        end_ip: "",
                        comment: ""
                    };
                    if(type === "dynamic") {
                        $scope.newRange.comment = "Dynamic";
                    }
                };

                // Cancel adding the new IP range.
                $scope.cancelAddRange = function() {
                    $scope.newRange = null;
                };

                // Return true if the IP range can be modified by the
                // authenticated user.
                $scope.ipRangeCanBeModified = function(range) {
                    if($scope.isSuperUser()) {
                        return true;
                    } else {
                        // Can only modify reserved and same user.
                        return (
                            range.type === "reserved" &&
                            range.user === UsersManager.getAuthUser().id);
                    }
                };

                // Return true if the IP range is in edit mode.
                $scope.isIPRangeInEditMode = function(range) {
                    return $scope.editIPRange === range;
                };

                // Toggle edit mode for the IP range.
                $scope.ipRangeToggleEditMode = function(range) {
                    $scope.deleteIPRange = null;
                    if($scope.isIPRangeInEditMode(range)) {
                        $scope.editIPRange = null;
                    } else {
                        $scope.editIPRange = range;
                    }
                };

                // Clear edit mode for the IP range.
                $scope.ipRangeClearEditMode = function() {
                    $scope.editIPRange = null;
                };

                // Return true if the IP range is in delete mode.
                $scope.isIPRangeInDeleteMode = function(range) {
                    return $scope.deleteIPRange === range;
                };

                // Enter delete mode for the IP range.
                $scope.ipRangeEnterDeleteMode = function(range) {
                    $scope.editIPRange = null;
                    $scope.deleteIPRange = range;
                };

                // Exit delete mode for the IP range.
                $scope.ipRangeCancelDelete = function() {
                    $scope.deleteIPRange = null;
                };

                // Perform the delete operation on the IP range.
                $scope.ipRangeConfirmDelete = function() {
                    IPRangesManager.deleteItem(
                        $scope.deleteIPRange).then(function() {
                            $scope.deleteIPRange = null;
                    });
                };

                // Load the reqiured managers.
                ManagerHelperService.loadManagers($scope, [
                    IPRangesManager, UsersManager]).then(
                        function() {
                            $scope.loading = false;
                        });
            }
        };
    }]);
